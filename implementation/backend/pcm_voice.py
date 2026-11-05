"""Actual Opus -> streaming PCM provider -> Opus path, with no default provider.

Provider input is an async PCM16/16k/mono iterator, available before listen.stop.
Provider output may have arbitrary PCM chunk boundaries. TTS begins only after
input ends. No ASR/LLM/TTS is fabricated by this transport/codec adapter.
"""
import asyncio
import contextlib
from .async_voice import AsyncVoice
from .voice import ProtocolError
from .device_store import Denied
from .opus import PcmFrames
from .vad import EnergyVad


class Turn:
    def __init__(self,mode):
        self.mode=mode
        self.vad=EnergyVad() if mode in ("auto","realtime") else None
        self.input=asyncio.Queue(maxsize=9) # eight PCM frames + reserved EOF slot
        self.stopped=asyncio.Event()
        self.count=0

    def feed(self,pcm):
        if self.stopped.is_set() or self.count>=500:raise ProtocolError("input duration budget")
        if self.input.qsize()>=8:raise ProtocolError("PCM provider backpressure")
        self.count+=1;self.input.put_nowait(pcm)
        return self.vad.feed(pcm) if self.vad else False

    def stop(self):
        if not self.stopped.is_set():
            self.stopped.set();self.input.put_nowait(None)

    async def chunks(self,check):
        while True:
            check()
            chunk=await self.input.get()
            check()
            if chunk is None:return
            yield chunk

    def close(self):
        self.stopped.set()
        while not self.input.empty():self.input.get_nowait()


class PcmVoice(AsyncVoice):
    def __init__(self,*args,codec,allow_realtime=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.codec=codec;self.turn=None
        self.decoder=codec.decoder(60)
        try:self.encoder=codec.encoder(60)
        except Exception:
            self.decoder.close();raise
        self.preroll=0;self.tail=0
        self.allow_realtime=allow_realtime
        self.barge_frames=[];self.barge_detector=EnergyVad()
        self.core.max_bytes=128000

    def provider_allowed(self):
        self.core.check_authorized()
        provider=self.provider
        if provider is None or not callable(getattr(provider,"respond_pcm",None)):
            raise Denied("PCM provider not configured")
        if getattr(provider,"pcm_format",None)!=(16000,1,"s16le"):raise ProtocolError("provider PCM format")
        categories=getattr(provider,"egress_categories",None)
        if (not isinstance(categories,frozenset) or not categories<={"audio","transcript","image","memory"}
            or any(not getattr(self.privacy,c+"_egress") for c in categories)):
            raise Denied("provider egress not authorized")

    async def cancel(self):
        self.barge_frames=[]
        if self.turn:self.turn.close();self.turn=None
        await super().cancel()

    async def receive(self,message):
        self.core.check_authorized()
        if self.closed:raise ProtocolError("closed")
        if isinstance(message,bytes):
            if self.core.state=="new" or self.core.state=="closed":raise ProtocolError("unexpected audio")
            pcm=self.decoder.decode(message) # Keep codec history across turns and aborts.
            if self.core.state=="ready":
                # Upstream can upload a bounded wake-word pre-roll before listen.detect/start.
                self.preroll+=1
                if self.preroll>64:raise ProtocolError("wake pre-roll budget")
                if self.barge_frames:
                    if len(self.barge_frames)>=8:raise ProtocolError("barge restart budget")
                    self.barge_frames.append(pcm)
                return
            if self.core.state=="responding" and self.turn and self.turn.mode=="realtime":
                self.tail+=1
                if self.tail>500:raise ProtocolError("realtime response budget")
                # The firmware only restarts listening after tts.stop while speaking.
                # Before the first TTS frame, a stop alone cannot restart its state.
                if not self.started:
                    self.barge_frames=[]
                    return
                if self.barge_detector.voiced(pcm):self.barge_frames.append(pcm)
                else:self.barge_frames=[]
                if len(self.barge_frames)>=2:
                    pending=self.barge_frames[-2:]
                    await self.cancel() # Emit stop; wait for the firmware's next listen.start.
                    self.barge_frames=pending
                return
            if self.core.state=="responding" and self.turn and self.turn.mode=="auto":
                self.tail+=1
                if self.tail>50:raise ProtocolError("auto response tail budget")
                return
            if self.core.state!="listening" or not self.turn:raise ProtocolError("unexpected audio")
            self.provider_allowed()
            if self.core.bytes+len(message)>self.core.max_bytes:raise ProtocolError("audio budget")
            ended=self.turn.feed(pcm);self.core.bytes+=len(message)
            if ended:self.turn.stop();self.core.state="responding";self.tail=0
            return
        if isinstance(message,dict) and message.get("type")=="listen":
            if message.get("state")=="detect":
                if self.core.state!="ready" or message.get("session_id")!=self.core.id or not isinstance(message.get("text"),str) or len(message["text"])>128:
                    raise ProtocolError("wake detection metadata")
                return # Do not send wake-word text to a provider or persist it.
            if message.get("state")=="start":
                self.provider_allowed()
                if len(self.tasks)>=2:raise Denied("previous provider has not stopped")
                mode=message.get("mode")
                if mode not in ("manual","auto","realtime"):raise ProtocolError("unknown listen mode")
                if mode=="realtime" and not self.allow_realtime:raise Denied("realtime requires explicit trusted AEC policy")
                # Reuse the common session/state validation, then select the local endpointer.
                self.core.receive(dict(message,mode="manual"))
                self.preroll=self.tail=0
                self.turn=Turn(mode)
                for pcm in self.barge_frames:self.turn.feed(pcm)
                self.barge_frames=[]
                generation=self.generation
                self.task=asyncio.create_task(self.run_pcm(self.turn,generation))
                self.tasks.add(self.task);self.task.add_done_callback(self.tasks.discard)
                return
            if message.get("state")=="stop":
                if self.core.state!="listening" or not self.turn or message.get("session_id")!=self.core.id:
                    raise ProtocolError("invalid stop")
                self.turn.stop();self.core.state="responding"
                return
        # Reuse hello, explicit abort, strict unknown-message handling.
        await super().receive(message)

    async def run_pcm(self,turn,generation):
        assembler=PcmFrames()
        next_packet=0.0
        def check():
            self.provider_allowed()
            if self.closed or generation!=self.generation:raise asyncio.CancelledError()
        async def packet(pcm,encoder):
            nonlocal next_packet
            # Do not burst a whole synthesized response into the device's small
            # playback queue. This await also keeps aborts responsive between frames.
            loop=asyncio.get_running_loop()
            if next_packet>loop.time():await asyncio.sleep(next_packet-loop.time())
            check()
            if not self.started:
                self.started=True
                await self.emit({"type":"tts","state":"start","session_id":self.core.id},generation)
            await self.emit(encoder.encode(pcm),generation)
            next_packet=loop.time()+0.060
        try:
            async with asyncio.timeout(self.timeout):
                encoder=self.encoder
                async for chunk in self.provider.respond_pcm(turn.chunks(check)):
                    check()
                    frames=assembler.push(chunk)
                    # Early provider output cannot force an unsolicited half-duplex TTS.
                    await turn.stopped.wait();check()
                    for frame in frames:await packet(frame,encoder)
                check()
                if not turn.stopped.is_set():raise ProtocolError("provider ended before input")
                for frame in assembler.finish():await packet(frame,encoder)
                if not self.started:
                    # Even an empty response must complete the firmware's turn.
                    self.started=True
                    await self.emit({"type":"tts","state":"start","session_id":self.core.id},generation)
        except asyncio.CancelledError:
            pass
        except Denied:
            self.core.close()
        except Exception:
            with contextlib.suppress(Exception):
                check()
                # These fields are consumed by the actual Application JSON parser.
                # Do not expose provider exceptions (which may contain private data).
                await self.emit({"type":"alert","status":"Voice unavailable",
                    "message":"The local voice handler could not complete this turn.",
                    "emotion":"neutral","session_id":self.core.id},generation)
                check()
                if not self.started:
                    self.started=True
                    await self.emit({"type":"tts","state":"start","session_id":self.core.id},generation)
        finally:
            turn.close()
            if generation==self.generation and not self.closed:
                if self.started:
                    self.started=False
                    with contextlib.suppress(Exception):
                        await self.emit({"type":"tts","state":"stop","session_id":self.core.id},generation)
                if self.turn is turn:self.turn=None
                if self.core.state!="closed":self.core.state="ready"

    async def close(self):
        if self.turn:self.turn.close();self.turn=None
        await super().close()
        self.decoder.close();self.encoder.close()
