"""Real libopus tests using synthetic tones only; optional existing FFmpeg oracle.

Run tools/run_voice_checks.py. No microphone, file media, model, or network egress.
"""
import array
import asyncio
import math
import os
import shutil
import struct
import subprocess
import unittest
from backend.opus import OpusLibrary,CodecError,PcmFrames
from backend.pcm_voice import PcmVoice
from backend.voice import Privacy
from backend.device_store import Denied
import test_service as harness
from test_service import HELLO,MAC

LIB=OpusLibrary(os.environ["STACKCHAN_TEST_OPUS_LIBRARY"]) if os.environ.get("STACKCHAN_TEST_OPUS_LIBRARY") else None


def tone(samples=9600):
    return b"".join(struct.pack("<h",int(7000*math.sin(2*math.pi*440*i/16000)+2000*math.sin(2*math.pi*880*i/16000))) for i in range(samples))


def encoded(pcm,duration=60):
    size=duration*16*2
    with LIB.encoder(duration) as encoder:
        return [encoder.encode(pcm[i:i+size]) for i in range(0,len(pcm),size)]


def correlation(a,b):
    a=array.array("h",a);b=array.array("h",b)
    return sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))


def ogg(packets):
    def page(packet,sequence,flags,granule):
        laces=bytes([255]*(len(packet)//255)+[len(packet)%255])
        data=bytearray(b"OggS\0"+bytes([flags])+struct.pack("<QII",granule,1,sequence)+bytes(4)+bytes([len(laces)])+laces+packet)
        crc=0
        for byte in data:
            crc^=byte<<24
            for _ in range(8):crc=((crc<<1)^ (0x04c11db7 if crc&0x80000000 else 0))&0xffffffff
        struct.pack_into("<I",data,22,crc)
        return bytes(data)
    head=b"OpusHead"+struct.pack("<BBHIhB",1,1,0,16000,0,0)
    tags=b"OpusTags"+struct.pack("<I",4)+b"test"+struct.pack("<I",0)
    return page(head,0,2,0)+page(tags,1,0,0)+b"".join(page(p,i+2,4 if i==len(packets)-1 else 0,(i+1)*2880) for i,p in enumerate(packets))


@unittest.skipUnless(LIB,"explicit installed Opus library required; run tools/run_voice_checks.py")
class NativeCodec(unittest.TestCase):
    def test_real_encode_decode_20_and_60ms(self):
        pcm=tone()
        for duration in (20,60):
            packets=encoded(pcm,duration)
            with LIB.decoder(duration) as decoder:decoded=b"".join(decoder.decode(p) for p in packets)
            self.assertEqual(len(decoded),len(pcm))
            # Account for real Opus lookahead; compare a long steady section.
            best=max(correlation(pcm[1000:15000],decoded[1000+delay*2:15000+delay*2]) for delay in range(80,161))
            self.assertGreater(best,.98)

    def test_real_packet_validation_and_state_lifetime(self):
        packet=encoded(tone(320),20)[0]
        with LIB.decoder(60) as decoder:
            for invalid in (packet,b"",b"\xff",bytes(4097)):
                with self.assertRaises(CodecError):decoder.decode(invalid)
        with self.assertRaises(CodecError):decoder.decode(packet)
        with LIB.encoder(60) as encoder:
            with self.assertRaises(CodecError):encoder.encode(b"\0\0")
        with self.assertRaises(CodecError):LIB.encoder(40)

    def test_ffmpeg_independent_raw_packet_decode(self):
        executable=shutil.which("ffmpeg")
        if executable is None:self.skipTest("existing FFmpeg unavailable")
        packets=encoded(tone())
        with LIB.decoder(60) as decoder:ours=b"".join(decoder.decode(p) for p in packets)
        run=subprocess.run([executable,"-hide_banner","-loglevel","error","-threads","1",
            "-protocol_whitelist","pipe","-f","ogg","-c:a","libopus","-i","pipe:0",
            "-threads","1","-ac","1","-ar","16000","-f","s16le","-c:a","pcm_s16le","pipe:1"],
            input=ogg(packets),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr.decode(errors="replace"))
        self.assertEqual(len(run.stdout),len(ours))
        self.assertGreater(correlation(ours[1000:],run.stdout[1000:]),.98)


class FrameBoundaries(unittest.TestCase):
    def test_fragmented_pcm_and_single_tail_padding(self):
        source=tone(1001);frames=PcmFrames();output=[]
        for offset in range(0,len(source),7):output+=frames.push(source[offset:offset+7])
        output+=frames.finish()
        self.assertEqual(b"".join(output),source+bytes(3840-len(source)))
        with self.assertRaises(CodecError):frames.push(b"x")

    def test_truncated_sample_and_duration_budget(self):
        frames=PcmFrames();frames.push(b"x")
        with self.assertRaises(CodecError):frames.finish()
        with self.assertRaises(CodecError):PcmFrames(max_bytes=2).push(b"1234")


@unittest.skipUnless(LIB,"explicit installed Opus library required; run tools/run_voice_checks.py")
class Streaming(unittest.IsolatedAsyncioTestCase):
    async def make(self,provider,privacy=None,authorize=lambda:None,timeout=3):
        self.output=[];self.packet_times=[]
        async def send(message):
            self.output.append(message)
            if isinstance(message,bytes):self.packet_times.append(asyncio.get_running_loop().time())
        voice=PcmVoice("test",authorize,send,provider,privacy,timeout=timeout,codec=LIB)
        self.addAsyncCleanup(voice.close)
        await voice.receive(HELLO)
        return voice

    async def start(self,voice,mode="manual"):
        await voice.receive({"type":"listen","state":"start","mode":mode,"session_id":"test"})

    async def stop(self,voice):
        await voice.receive({"type":"listen","state":"stop","session_id":"test"})

    async def test_incremental_input_before_stop_and_fragmented_output(self):
        seen=asyncio.Event();input_pcm=[]
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for chunk in chunks:input_pcm.append(chunk);seen.set()
                data=tone(1001)
                for i in range(0,len(data),17):yield data[i:i+17]
        voice=await self.make(Provider());await self.start(voice)
        packet=encoded(tone(960))[0]
        await voice.receive(packet);await asyncio.wait_for(seen.wait(),1)
        self.assertEqual(len(input_pcm[0]),1920)
        self.assertEqual(len(self.output),1) # Provider consumed PCM while input is still live.
        task=voice.task;await self.stop(voice);await task
        self.assertEqual([m["state"] for m in self.output if isinstance(m,dict) and m.get("type")=="tts"],["start","stop"])
        packets=[m for m in self.output if isinstance(m,bytes)];self.assertEqual(len(packets),2)
        self.assertGreaterEqual(self.packet_times[1]-self.packet_times[0],.050)
        with LIB.decoder(60) as decoder:self.assertEqual(sum(len(decoder.decode(p)) for p in packets),3840)

    async def test_abort_blocks_provider_which_yields_after_cancellation(self):
        sent=asyncio.Event()
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for _ in chunks:pass
                yield tone(960);sent.set()
                try:await asyncio.sleep(10)
                except asyncio.CancelledError:yield tone(960)
        voice=await self.make(Provider());await self.start(voice)
        await voice.receive(encoded(tone(960))[0]);await self.stop(voice)
        await asyncio.wait_for(sent.wait(),1)
        await voice.receive({"type":"abort","session_id":"test"})
        await asyncio.sleep(.02)
        self.assertEqual(len([x for x in self.output if isinstance(x,bytes)]),1)
        self.assertEqual(voice.core.state,"ready")

    async def test_input_queue_backpressure_and_no_egress(self):
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset({"audio"})
            async def respond_pcm(self,chunks):
                await asyncio.sleep(10);yield tone(960)
        voice=await self.make(Provider())
        with self.assertRaises(Denied):await self.start(voice)
        voice.privacy=Privacy(audio_egress=True);await self.start(voice)
        for packet in encoded(tone(960*8)):await voice.receive(packet)
        with self.assertRaises(ValueError):await voice.receive(encoded(tone(960))[0])

    async def test_auto_endpoint_wake_preroll_and_tail(self):
        observed=[]
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for chunk in chunks:observed.append(chunk)
                await asyncio.sleep(.02)
                yield tone(960)
        voice=await self.make(Provider())
        with LIB.encoder(60) as encoder:
            await voice.receive(encoder.encode(tone(960)))
            await voice.receive({"type":"listen","state":"detect","text":"synthetic wake","session_id":"test"})
            await self.start(voice,"auto");task=voice.task
            for pcm in [tone(960)]*4+[bytes(1920)]*18:
                await voice.receive(encoder.encode(pcm));await asyncio.sleep(.001)
                if voice.core.state=="responding":break
            self.assertEqual(voice.core.state,"responding")
            await voice.receive(encoder.encode(bytes(1920))) # bounded tail, not another user turn
            await task
        self.assertGreater(len(observed),10)
        self.assertEqual(voice.core.state,"ready")
        self.assertEqual(len([x for x in self.output if isinstance(x,bytes)]),1)

    async def test_realtime_gate_and_barge_restart_preserves_pcm(self):
        entered=asyncio.Event();second_input=asyncio.Event();calls=[]
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                index=len(calls);calls.append([])
                async for pcm in chunks:
                    calls[index].append(pcm)
                    if index==1:second_input.set()
                yield tone(960)
                if index==0:
                    entered.set()
                    try:await asyncio.sleep(10)
                    except asyncio.CancelledError:yield tone(960)
        voice=await self.make(Provider())
        with self.assertRaises(Denied):await self.start(voice,"realtime")
        voice.allow_realtime=True
        with LIB.encoder(60) as encoder:
            await self.start(voice,"realtime")
            await voice.receive(encoder.encode(tone(960)));await self.stop(voice)
            await asyncio.wait_for(entered.wait(),1)
            await voice.receive(encoder.encode(tone(960)))
            await voice.receive(encoder.encode(tone(960)))
            self.assertEqual(voice.core.state,"ready")
            self.assertEqual(self.output[-1]["state"],"stop")
            await self.start(voice,"realtime")
            await asyncio.wait_for(second_input.wait(),1)
            task=voice.task;await self.stop(voice);await task
        self.assertEqual(len(calls[1]),2)
        self.assertEqual(len([x for x in self.output if isinstance(x,bytes)]),2)

    async def test_revocation_timeout_wrong_packet_and_firmware_alert(self):
        allowed=True
        def authorize():
            if not allowed:raise Denied("synthetic revoke")
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for _ in chunks:pass
                await asyncio.sleep(1);yield tone(960)
        voice=await self.make(Provider(),authorize=authorize,timeout=.03)
        await self.start(voice)
        with self.assertRaises(CodecError):await voice.receive(encoded(tone(320),20)[0])
        await voice.receive(encoded(tone(960))[0]);task=voice.task;await self.stop(voice);await task
        self.assertTrue(any(isinstance(x,dict) and x.get("type")=="alert" and all(isinstance(x.get(k),str) for k in ("status","message","emotion")) for x in self.output))
        self.assertEqual([x["state"] for x in self.output if isinstance(x,dict) and x.get("type")=="tts"],["start","stop"])
        await self.start(voice);allowed=False
        with self.assertRaises(Denied):await voice.receive(encoded(tone(960))[0])
        self.assertEqual(voice.core.state,"closed")

    async def test_realtime_before_tts_and_empty_provider_complete_turn(self):
        waiting=asyncio.Event();release=asyncio.Event()
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for _ in chunks:pass
                waiting.set();await release.wait()
                if False:yield b""
        voice=await self.make(Provider());voice.allow_realtime=True
        await self.start(voice,"realtime")
        task=voice.task;await self.stop(voice)
        await asyncio.wait_for(waiting.wait(),1)
        for packet in encoded(tone(1920)):await voice.receive(packet)
        self.assertEqual(voice.core.state,"responding")
        self.assertEqual(len(self.output),1)
        release.set();await task
        self.assertEqual([x["state"] for x in self.output if isinstance(x,dict) and x.get("type")=="tts"],["start","stop"])
        self.assertEqual(voice.core.state,"ready")


@unittest.skipUnless(LIB,"explicit installed Opus library required; run tools/run_voice_checks.py")
class PcmHttpWs(unittest.IsolatedAsyncioTestCase):
    http=harness.Integration.http;ws=harness.Integration.ws;bind=harness.Integration.bind
    asyncSetUp=harness.Integration.asyncSetUp;asyncTearDown=harness.Integration.asyncTearDown

    async def test_avatar_control_relay_real_20ms_both_directions(self):
        from backend.control import encode,decode
        token=await self.bind()
        self.s.hub.policy.calls=self.s.hub.policy.camera=self.s.hub.policy.microphone=True
        self.s.control_permissions.add((self.a.subject,MAC,9))
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await app.send(encode(9,MAC.encode()+b"synthetic"))
        self.assertEqual(await device.recv(),encode(9,b"synthetic"))
        await device.send(encode(11))
        self.assertEqual(await device.recv(),encode(5,MAC.encode()))
        self.assertEqual(await device.recv(),encode(24,MAC.encode()))
        self.assertEqual(await app.recv(),encode(11))
        with LIB.encoder(20) as phone_encoder, LIB.encoder(20) as device_encoder, LIB.decoder(20) as phone_decoder, LIB.decoder(20) as device_decoder:
            for _ in range(3):
                await app.send(encode(1,MAC.encode()+phone_encoder.encode(tone(320))))
                kind,packet=decode(await device.recv());self.assertEqual(kind,1)
                self.assertEqual(len(device_decoder.decode(packet)),640)
                await device.send(encode(1,device_encoder.encode(tone(320))))
                kind,packet=decode(await app.recv());self.assertEqual(kind,1)
                self.assertEqual(len(phone_decoder.decode(packet)),640)
        await app.send(encode(12))
        self.assertEqual(await device.recv(),encode(12))
        self.assertEqual(await device.recv(),encode(6));self.assertEqual(await device.recv(),encode(25))
        await app.send(encode(1,MAC.encode()+encoded(tone(320),20)[0]))
        self.assertEqual(await app.recv(),("close",1008))

    async def test_reconnect_new_session_rejects_stale_session_and_decodes(self):
        consumed=asyncio.Event();cancelled=asyncio.Event()
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                try:
                    async for _ in chunks:consumed.set()
                    yield tone(960)
                except asyncio.CancelledError:
                    cancelled.set();raise
        self.s.codec=LIB;self.s.provider=Provider()
        token=await self.bind()
        old=await self.ws("/xiaozhi/v1/",token)
        await old.send(HELLO);old_id=(await old.recv())["session_id"]
        await old.send({"type":"listen","state":"start","mode":"manual","session_id":old_id})
        await old.send(encoded(tone(960))[0]);await asyncio.wait_for(consumed.wait(),1)
        await old.close();await asyncio.wait_for(cancelled.wait(),1)
        stale=await self.ws("/xiaozhi/v1/",token)
        await stale.send(HELLO);new_id=(await stale.recv())["session_id"]
        self.assertNotEqual(old_id,new_id)
        await stale.send({"type":"listen","state":"start","mode":"manual","session_id":old_id})
        self.assertEqual(await stale.recv(),("close",1002))
        fresh=await self.ws("/xiaozhi/v1/",token)
        await fresh.send(HELLO);fresh_id=(await fresh.recv())["session_id"]
        await fresh.send({"type":"listen","state":"start","mode":"manual","session_id":fresh_id})
        await fresh.send(encoded(tone(960))[0])
        await fresh.send({"type":"listen","state":"stop","session_id":fresh_id})
        self.assertEqual((await fresh.recv())["state"],"start")
        with LIB.decoder(60) as decoder:self.assertEqual(len(decoder.decode(await fresh.recv())),1920)
        self.assertEqual((await fresh.recv())["state"],"stop")

    async def test_actual_websocket_opus_pcm_stream_and_unbind(self):
        seen=asyncio.Event()
        class Provider:
            pcm_format=(16000,1,"s16le");egress_categories=frozenset()
            async def respond_pcm(self,chunks):
                async for chunk in chunks:
                    if len(chunk)!=1920:raise ValueError("wrong PCM")
                    seen.set()
                yield tone(1920)
        self.s.codec=LIB;self.s.provider=Provider()
        token=await self.bind();client=await self.ws("/xiaozhi/v1/",token,"Protocol-Version: 1\r\n")
        await client.send(HELLO);ident=(await client.recv())["session_id"]
        await client.send({"type":"listen","state":"start","mode":"manual","session_id":ident})
        for packet in encoded(tone(960*3)):
            await client.send(packet);await asyncio.sleep(.002)
        await asyncio.wait_for(seen.wait(),1)
        await client.send({"type":"listen","state":"stop","session_id":ident})
        self.assertEqual((await client.recv())["state"],"start")
        with LIB.decoder(60) as decoder:
            self.assertEqual(len(decoder.decode(await client.recv())),1920)
            self.assertEqual(len(decoder.decode(await client.recv())),1920)
        self.assertEqual((await client.recv())["state"],"stop")
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual(await client.recv(),("close",1008))
