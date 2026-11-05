"""Cancellable provider adapter over the observed Xiaozhi v1 message shapes."""
import asyncio
import contextlib
from .voice import VoiceSession, Privacy, ProtocolError
from .device_store import Denied

class AsyncVoice:
    def __init__(self, ident, authorize, send, provider=None, privacy=None, timeout=30):
        self.core = VoiceSession(ident, authorize)
        self.send, self.provider = send, provider
        self.privacy, self.timeout = privacy or Privacy(), timeout
        self.generation, self.task = 0, None
        self.tasks = set()
        self.started = False
        self.closed = False
        self.output_lock = asyncio.Lock()

    async def emit(self, message, generation):
        async with self.output_lock:
            if self.closed or generation != self.generation: return
            self.core.check_authorized()
            await self.send(message)

    async def cancel(self):
        self.generation += 1
        if self.task and not self.task.done(): self.task.cancel()
        self.task = None
        self.core.clear()
        self.core.state = "ready"
        if self.started:
            self.started = False
            await self.emit({"type":"tts","state":"stop","session_id":self.core.id},self.generation)

    async def receive(self, message):
        self.core.check_authorized()
        if self.closed: raise ProtocolError("closed")
        if isinstance(message,dict) and message.get("type") == "abort":
            if self.core.state == "new" or message.get("session_id") != self.core.id:
                raise ProtocolError("invalid abort session")
            await self.cancel()
            return
        if isinstance(message,dict) and message.get("type") == "listen" and message.get("state") == "stop":
            if self.core.state != "listening" or message.get("session_id") != self.core.id:
                raise ProtocolError("invalid stop")
            frames = tuple(self.core.audio)
            self.core.clear()
            self.core.state = "ready"
            if not frames: return
            if self.provider is None: raise Denied("voice provider not configured")
            categories = self.provider.egress_categories
            if (not isinstance(categories,frozenset)
                or not categories <= {"audio","transcript","image","memory"}
                or any(not getattr(self.privacy,k+"_egress") for k in categories)):
                raise Denied("provider egress not authorized")
            await self.cancel()
            generation = self.generation
            self.core.state = "responding"
            self.task = asyncio.create_task(self.run(frames,generation))
            self.tasks.add(self.task)
            self.task.add_done_callback(self.tasks.discard)
            return
        for reply in self.core.receive(message):
            await self.emit(reply,self.generation)

    async def run(self, frames, generation):
        total = 0
        try:
            async with asyncio.timeout(self.timeout):
                async for packet in self.provider.respond(frames):
                    if self.closed or generation != self.generation: return
                    if not isinstance(packet,bytes) or not packet: raise ProtocolError("invalid provider audio")
                    total += len(packet)
                    if total > self.core.max_bytes: raise ProtocolError("provider output budget exceeded")
                    if not self.started:
                        self.started = True
                        await self.emit({"type":"tts","state":"start","session_id":self.core.id},generation)
                    await self.emit(packet,generation)
        except asyncio.CancelledError:
            return
        except Denied:
            self.core.close()
            return
        except Exception:
            # Never return raw provider exception text, which can contain credentials/data.
            with contextlib.suppress(Exception):
                await self.emit({"type":"error","code":"provider_failed","session_id":self.core.id},generation)
        finally:
            if generation == self.generation and not self.closed:
                if self.started:
                    self.started = False
                    with contextlib.suppress(Exception):
                        await self.emit({"type":"tts","state":"stop","session_id":self.core.id},generation)
                if self.core.state != "closed": self.core.state = "ready"

    async def close(self):
        self.closed = True
        self.generation += 1
        self.core.close()
        for task in self.tasks: task.cancel()
        if self.tasks:
            await asyncio.wait(self.tasks,timeout=0.2)
