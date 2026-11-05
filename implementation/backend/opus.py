"""Stateful raw Opus/PCM16 adapter using an explicitly selected libopus.

No downloader, media capture, subprocess, Ogg framing, or network access. C ABI
signatures follow the opus.h already supplied by the pinned Flutter dependency.
"""
import ctypes as C
import os
from pathlib import Path
import sys


class CodecError(ValueError):
    pass


class OpusLibrary:
    rate=16000
    channels=1
    max_packet=4096

    def __init__(self,path):
        path=Path(path).resolve(strict=True)
        if not path.is_file() or sys.byteorder!="little":raise CodecError("unsupported codec host")
        # A caller-controlled local path, never a request parameter or DLL search.
        self.lib=C.CDLL(str(path),**({"winmode":0x1100} if os.name=="nt" else {}))
        signatures={
            "opus_get_version_string":([],C.c_char_p),
            "opus_encoder_create":([C.c_int32,C.c_int,C.c_int,C.POINTER(C.c_int)],C.c_void_p),
            "opus_decoder_create":([C.c_int32,C.c_int,C.POINTER(C.c_int)],C.c_void_p),
            "opus_encoder_destroy":([C.c_void_p],None),
            "opus_decoder_destroy":([C.c_void_p],None),
            "opus_encode":([C.c_void_p,C.POINTER(C.c_int16),C.c_int,C.POINTER(C.c_ubyte),C.c_int32],C.c_int32),
            "opus_decode":([C.c_void_p,C.POINTER(C.c_ubyte),C.c_int32,C.POINTER(C.c_int16),C.c_int,C.c_int],C.c_int),
            "opus_packet_get_nb_samples":([C.POINTER(C.c_ubyte),C.c_int32,C.c_int32],C.c_int),
            "opus_packet_get_nb_channels":([C.POINTER(C.c_ubyte)],C.c_int),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.lib,name);fn.argtypes=args;fn.restype=result
        self.version=self.lib.opus_get_version_string().decode("ascii")

    def packet(self,data,duration_ms):
        if type(data) is not bytes or not 1<=len(data)<=self.max_packet:raise CodecError("invalid Opus packet length")
        raw=(C.c_ubyte*len(data)).from_buffer_copy(data)
        if self.lib.opus_packet_get_nb_channels(raw)!=1:raise CodecError("mono Opus required")
        if self.lib.opus_packet_get_nb_samples(raw,len(data),self.rate)!=self.rate*duration_ms//1000:
            raise CodecError("Opus duration differs from negotiated frame")
        return raw

    def encoder(self,duration_ms=60):return Encoder(self,duration_ms)
    def decoder(self,duration_ms=60):return Decoder(self,duration_ms)


class State:
    def __init__(self,library,duration_ms,kind):
        if duration_ms not in (20,60):raise CodecError("unsupported frame duration")
        self.library,self.duration_ms,self.kind=library,duration_ms,kind
        self.samples=library.rate*duration_ms//1000
        error=C.c_int()
        if kind=="encoder":
            self.handle=library.lib.opus_encoder_create(library.rate,1,2048,C.byref(error)) # OPUS_APPLICATION_VOIP
        else:self.handle=library.lib.opus_decoder_create(library.rate,1,C.byref(error))
        if not self.handle or error.value!=0:
            self.close();raise CodecError("Opus state initialization failed")

    def check(self):
        if not self.handle:raise CodecError("codec state closed")

    def close(self):
        if getattr(self,"handle",None):
            getattr(self.library.lib,"opus_"+self.kind+"_destroy")(self.handle)
            self.handle=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class Encoder(State):
    def __init__(self,library,duration_ms):super().__init__(library,duration_ms,"encoder")

    def encode(self,pcm):
        self.check()
        if type(pcm) is not bytes or len(pcm)!=self.samples*2:raise CodecError("exact PCM16 frame required")
        source=(C.c_int16*self.samples).from_buffer_copy(pcm)
        output=(C.c_ubyte*self.library.max_packet)()
        count=self.library.lib.opus_encode(self.handle,source,self.samples,output,len(output))
        if count<=0:raise CodecError("Opus encode failed")
        packet=bytes(output[:count]);self.library.packet(packet,self.duration_ms)
        return packet


class Decoder(State):
    def __init__(self,library,duration_ms):super().__init__(library,duration_ms,"decoder")

    def decode(self,packet):
        self.check()
        source=self.library.packet(packet,self.duration_ms)
        output=(C.c_int16*self.samples)()
        count=self.library.lib.opus_decode(self.handle,source,len(packet),output,self.samples,0)
        if count!=self.samples:raise CodecError("Opus decode failed or wrong duration")
        return bytes(output)


class PcmFrames:
    """Reassemble arbitrary byte chunks; preserve 16-bit sample boundaries."""
    def __init__(self,frame_bytes=1920,max_bytes=960000):
        self.frame_bytes,self.max_bytes=frame_bytes,max_bytes
        self.buffer=bytearray();self.total=0;self.finished=False

    def push(self,chunk):
        if self.finished or type(chunk) is not bytes or not 0<len(chunk)<=65536:raise CodecError("invalid PCM chunk")
        self.total+=len(chunk)
        if self.total>self.max_bytes:raise CodecError("PCM duration budget exceeded")
        self.buffer.extend(chunk)
        result=[]
        while len(self.buffer)>=self.frame_bytes:
            result.append(bytes(self.buffer[:self.frame_bytes]));del self.buffer[:self.frame_bytes]
        return result

    def finish(self):
        if self.finished:raise CodecError("PCM stream already finished")
        self.finished=True
        if len(self.buffer)%2:raise CodecError("truncated PCM16 sample")
        if not self.buffer:return []
        frame=bytes(self.buffer)+bytes(self.frame_bytes-len(self.buffer));self.buffer.clear()
        return [frame] # At most one negotiated frame of trailing silence.
