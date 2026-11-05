"""Loopback-only development HTTP/WebSocket subset (RFC 6455).
No TLS, extensions, fragmentation, keep-alive HTTP or production exposure.
"""
import asyncio
import base64
import hashlib
import json
import struct

LIMIT = 65535
WS_LIMIT = 256 * 1024

def object_json(data):
    def unique(pairs):
        value = {}
        for k,v in pairs:
            if k in value: raise ValueError("duplicate JSON key")
            value[k] = v
        return value
    value = json.loads(data, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
    if not isinstance(value,dict): raise ValueError("JSON object required")
    return value

async def request(reader):
    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    if len(raw)>16384: raise ValueError("headers too large")
    lines = raw.decode("ascii").split("\r\n")
    method,target,version = lines[0].split(" ")
    if version != "HTTP/1.1" or not target.startswith("/") or target.startswith("//"):
        raise ValueError("invalid request target")
    headers={}
    for line in lines[1:-2]:
        key,value=line.split(":",1);key=key.lower()
        if key in headers or not key or key.strip()!=key: raise ValueError("invalid/duplicate header")
        headers[key]=value.strip()
    if "host" not in headers or "transfer-encoding" in headers: raise ValueError("unsupported framing")
    # Local browser pages must not be able to drive this native development API.
    if "origin" in headers: raise ValueError("browser origins are not enabled")
    size=headers.get("content-length","0")
    if not size.isdigit() or int(size)>LIMIT: raise ValueError("invalid body length")
    body=await asyncio.wait_for(reader.readexactly(int(size)),5)
    return method,target,headers,body

async def response(writer,status,data):
    body=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
    labels={200:"OK",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",409:"Conflict",501:"Not Implemented",500:"Internal Server Error"}
    writer.write((f"HTTP/1.1 {status} {labels.get(status,'Error')}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode()+body)
    await asyncio.wait_for(writer.drain(),2)

class WebSocket:
    def __init__(self, reader, writer):
        self.reader,self.writer=reader,writer
        self.lock=asyncio.Lock()
        self.closed=False
        self.authorize=None
        self.allow_camera_ack=False

    async def upgrade(self,method,headers):
        key=headers.get("sec-websocket-key","")
        if (method!="GET" or headers.get("upgrade","").lower()!="websocket"
            or "upgrade" not in {v.strip().lower() for v in headers.get("connection","").split(",")}
            or headers.get("sec-websocket-version")!="13"
            or "sec-websocket-extensions" in headers
            or "sec-websocket-protocol" in headers):
            raise ValueError("unsupported websocket handshake")
        if len(base64.b64decode(key,validate=True))!=16: raise ValueError("invalid websocket key")
        accept=base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.writer.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        await self.writer.drain()

    async def frame(self,opcode,data,authorize=None):
        if self.closed:return
        if len(data)>WS_LIMIT:raise ValueError("frame too large")
        header=(bytes([0x80|opcode,len(data)]) if len(data)<126 else
                bytes([0x80|opcode,126])+struct.pack(">H",len(data)) if len(data)<65536 else
                bytes([0x80|opcode,127])+struct.pack(">Q",len(data)))
        async with self.lock:
            if self.closed:return
            if opcode in (1,2) and self.authorize:self.authorize()
            if authorize:authorize()
            self.writer.write(header+data)
            await asyncio.wait_for(self.writer.drain(),2)

    async def send(self,value,authorize=None):
        await self.frame(2,value,authorize) if isinstance(value,bytes) else await self.frame(1,json.dumps(value,separators=(",",":")).encode(),authorize)

    async def receive(self):
        while not self.closed:
            first,second=await self.reader.readexactly(2)
            opcode=first&15;size=second&127
            if first&0x70 or not first&0x80 or not second&0x80 or opcode not in (1,2,8,9,10):
                raise ValueError("unsupported frame flags/opcode")
            if opcode>=8 and size>125:raise ValueError("invalid control frame")
            if size==127:
                size=struct.unpack(">Q",await self.reader.readexactly(8))[0]
                if size<65536 or size>WS_LIMIT:raise ValueError("invalid frame length")
            if size==126:
                size=struct.unpack(">H",await self.reader.readexactly(2))[0]
                if size<126:raise ValueError("noncanonical length")
            mask=await self.reader.readexactly(4)
            body=await self.reader.readexactly(size)
            body=bytes(v^mask[i%4] for i,v in enumerate(body))
            if opcode==8:
                if len(body)==1:raise ValueError("invalid close")
                if len(body)>=2:
                    code=struct.unpack(">H",body[:2])[0]
                    if code not in (1000,1001,1002,1003,1007,1008,1009,1010,1011,1012,1013,1014) and not 3000<=code<=4999:
                        raise ValueError("invalid close code")
                    body[2:].decode("utf-8")
                await self.close()
                return None
            if opcode==9:
                await self.frame(10,body);continue
            if opcode==10:continue
            if opcode==2:return body
            text=body.decode("utf-8")
            if self.allow_camera_ack and text in ("camera stream started","camera stream stopped"):return text
            return object_json(text)

    async def close(self,code=1000):
        if not self.closed:
            try:await self.frame(8,struct.pack(">H",code))
            finally:
                self.closed=True
                self.writer.close()
