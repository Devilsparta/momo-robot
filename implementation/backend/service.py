"""Runnable loopback development service; no unauthenticated bootstrap endpoint."""
import asyncio
import contextlib
import secrets
import sqlite3
from urllib.parse import urlsplit,parse_qs
from .device_store import DeviceStore,Principal,Denied,mac
from .access import Access,ClaimCodes
from .pairing import Pairing
from .identity import Identity
from .ota import no_update
from .control_hub import ControlHub
from .dance import Dances
from .memory import Memory
from .tool_policy import ToolPolicy
from .async_voice import AsyncVoice
from .pcm_voice import PcmVoice
from .voice import Privacy
from .control import decode,encode,route
from .transport import request,response,object_json,WebSocket

class Service:
    def __init__(self,db=":memory:",provider=None,privacy=None,codec=None,allow_realtime=False,releases=None):
        self.db=sqlite3.connect(db)
        self.claims=ClaimCodes()
        self.store=DeviceStore(self.db,self.claims.verify)
        self.access=Access(self.store)
        self.pairing=Pairing(self.store,self.claims,self.access)
        self.identity=Identity(self.store,self.access)
        self.memory=Memory(self.store)
        self.dances=Dances(self.store)
        self.tools=ToolPolicy(self.store)
        self.provider,self.privacy,self.codec=provider,privacy or Privacy(),codec
        self.allow_realtime=allow_realtime
        self.releases=releases
        self.heartbeat_interval=3.0
        self.server=None
        self.clients=set()
        self.tasks=set()
        self.control_devices={}
        self.control_apps={}
        self.hub=ControlHub(self)
        self.control_permissions=set() # (owner,mac,type); confirmed owners receive avatar/motion only.

    async def start(self,port=0,host="127.0.0.1"):
        if host!="127.0.0.1":raise ValueError("development service binds only 127.0.0.1")
        self.server=await asyncio.start_server(self.handle,host,port,limit=16384)
        self.port=self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self.server:self.server.close();await self.server.wait_closed()
        for writer in list(self.clients):writer.close()
        if self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)
        self.db.close()

    def binding(self,grant,address=None):
        if grant.principal.kind=="device":
            if grant.binding is None:raise Denied("device is not bound")
            if address and mac(address)!=grant.binding[1]:raise Denied("wrong device")
            self.store.check_session(grant.binding)
            return grant.binding
        return self.store.session(grant.principal,address)

    def check(self,headers,scope,binding):
        grant=self.access.authenticate(headers)
        self.access.require(grant,scope)
        self.store.check_session(binding)

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task);self.clients.add(writer)
        ws=None
        try:
            method,target,headers,body=await request(reader)
            parsed=urlsplit(target)
            if parsed.fragment:raise ValueError("fragment not allowed")
            params=parse_qs(parsed.query,strict_parsing=True)
            if any(len(v)!=1 for v in params.values()):raise ValueError("duplicate query")
            query={k:v[0] for k,v in params.items()}
            path=parsed.path
            if path=="/health" and method=="GET":
                await response(writer,200,{"status":"development","provider":self.provider is not None,"bootstrap":"disabled"})
                return
            if method=="POST" and path in ("/private/v1/identity/challenge","/private/v1/identity/prove"):
                data=object_json(body)
                if path.endswith("challenge"):
                    if set(data)!={"mac"}:raise ValueError("challenge fields")
                    result=self.identity.challenge(data["mac"])
                else:
                    if set(data)!={"challenge_id","proof"}:raise ValueError("proof fields")
                    result=self.identity.prove(data["challenge_id"],data["proof"])
                    if result["state"]=="bound":
                        device=self.access.authenticate({"authorization":"Bearer "+result["device_token"]})
                        owner,address,_=device.binding
                        self.control_permissions.update((owner,address,k) for k in (3,4))
                await response(writer,200,{"code":0,"message":"","data":result})
                return
            grant=self.access.authenticate(headers)
            if headers.get("upgrade","").lower()=="websocket":
                if path not in ("/xiaozhi/v1/","/stackChan/ws"):raise ValueError("unknown websocket")
                scope="voice" if path=="/xiaozhi/v1/" else "control"
                self.access.require(grant,scope)
                address=query.get("mac") or headers.get("device-id")
                binding=self.binding(grant,address)
                if scope=="voice":
                    if grant.principal.kind!="device":raise Denied("device required")
                    if headers.get("protocol-version","1")!="1":raise ValueError("only voice v1 supported")
                elif query.get("deviceType")!=("StackChan" if grant.principal.kind=="device" else "App"):
                    raise ValueError("invalid deviceType")
                ws=WebSocket(reader,writer)
                ws.allow_camera_ack=scope=="control" and grant.principal.kind=="device"
                ws.authorize=lambda:self.check(headers,scope,binding)
                await ws.upgrade(method,headers)
                await self.websocket(ws,headers,scope,binding,grant)
                return
            data=object_json(body) if body else {}
            if path=="/xiaozhi/ota/" and method=="POST":
                self.access.require(grant,"device","device")
                self.binding(grant,headers.get("device-id"))
                if "device-id" not in headers:raise ValueError("Device-Id required")
                result=no_update(data)
                release=self.releases.offer(grant.principal.subject) if self.releases else None
                if release:result["private_release"]=release
                await response(writer,200,result)
                return
            if path.startswith("/private/v1/releases/") and method=="GET":
                self.access.require(grant,"device","device")
                self.binding(grant)
                artifact=self.releases.download(grant.principal.subject,path.rsplit("/",1)[-1]) if self.releases else None
                if artifact is None:raise Denied("release unavailable")
                # Headers are generated locally; no redirects, paths or storage IDs from requests.
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(artifact)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode())
                try:
                    for offset in range(0,len(artifact),4096):
                        self.check(headers,"device",self.binding(grant))
                        if self.releases.download(grant.principal.subject,path.rsplit("/",1)[-1]) is not artifact:raise Denied("release withdrawn")
                        writer.write(artifact[offset:offset+4096]);await writer.drain()
                        await asyncio.sleep(0)
                except Denied:
                    return # close a truncated binary body; never append a second HTTP response
                return
            status,result=await self.http(method,path,query,data,grant)
            await response(writer,status,{"code":0 if status==200 else status,"message":"" if status==200 else "unavailable","data":result})
        except Denied:
            if ws:
                with contextlib.suppress(Exception):await ws.close(1008)
            else:
                with contextlib.suppress(Exception):await response(writer,403,{"code":403,"message":"access denied","data":None})
        except (ValueError,KeyError,TypeError,UnicodeError,asyncio.LimitOverrunError):
            if ws:
                with contextlib.suppress(Exception):await ws.close(1002)
            else:
                with contextlib.suppress(Exception):await response(writer,400,{"code":400,"message":"invalid request","data":None})
        except (asyncio.IncompleteReadError,ConnectionError,TimeoutError):
            pass
        except Exception:
            if ws:
                with contextlib.suppress(Exception):await ws.close(1011)
            else:
                with contextlib.suppress(Exception):await response(writer,500,{"code":500,"message":"internal error","data":None})
        finally:
            writer.close()
            with contextlib.suppress(Exception):await writer.wait_closed()
            self.clients.discard(writer);self.tasks.discard(task)

    async def http(self,method,path,query,data,grant):
        p=grant.principal
        if path=="/private/v1/claim" and method=="POST":
            self.access.require(grant,"claim","device")
            return 200,self.pairing.start(grant)
        if path=="/private/v1/claim" and method=="DELETE":
            return 200,self.pairing.cancel(grant,data["claim_id"])
        if path=="/private/v1/claim/status" and method=="GET":
            return 200,self.pairing.poll(grant,query["claim_id"])
        if path=="/private/v1/bind" and method=="POST":
            self.access.require(grant,"manage","user")
            result=self.pairing.confirm(p,data["mac"],data["code"])
            address=mac(data["mac"])
            self.control_permissions.update((p.subject,address,kind) for kind in (3,4))
            return 200,result
        if path=="/private/v1/tools/call" and method=="POST":
            self.access.require(grant,"tools")
            binding=self.binding(grant,data.get("mac"))
            try:
                result=await self.tools.call(binding,data["name"],data["arguments"])
                self.access.require(grant,"tools")
                return 200,result
            except TimeoutError:
                return 409,{"reason":"tool deadline exceeded"}
        if path.startswith("/private/v1/memory"):
            self.access.require(grant,"manage","user")
            address=data.get("mac",query.get("mac"))
            if path.endswith("/consent") and method=="PUT":
                self.memory.consent(p,address,data["enabled"]);return 200,True
            if path=="/private/v1/memory":
                if method=="GET":return 200,self.memory.export(p,address)
                if method=="POST":return 200,{"id":self.memory.add(p,address,data["text"])}
                if method=="DELETE":
                    self.memory.delete(p,address,data["id"]);return 200,True
            return 404,None
        if path=="/private/v1/config":
            self.access.require(grant,"manage","user")
            if method=="GET":return 200,self.store.get_config(p,query["mac"])
            if method=="PUT":
                self.store.configure(p,data["mac"],data["config"]);return 200,True
        if path in ("/stackChan/v2/dance","/stackChan/v2/danceData","/stackChan/danceData","/stackChan/dance"):
            self.access.require(grant,"manage","user")
            if path.endswith("danceData") or (path=="/stackChan/dance" and "id" in query):
                if method=="GET":return 200,self.dances.result(self.dances.owned(p,query["id"]))
                return 501,None
            if path=="/stackChan/v2/dance":
                if method=="GET":return 200,self.dances.list(p,query["mac"])
                if method=="POST":return 200,self.dances.create(p,data)
                if method=="PUT":return 200,self.dances.update(p,data)
                if method=="DELETE":return 200,self.dances.delete(p,data["id"])
            return 501,None
        if path=="/private/v1/capabilities" and method=="GET":
            self.access.require(grant,"control","user")
            binding=self.binding(grant,query["mac"])
            commands={13,14,17,21}
            commands.update(k for k in (3,4,5,9,18,24,26) if self.hub.permitted(binding,k))
            if 4 in commands:commands.add(20)
            for on,off in ((5,6),(9,12),(18,19),(24,25)):
                if on in commands:commands.add(off)
            if 9 in commands:commands.add(1)
            if 18 in commands:commands.add(2)
            return 200,{"mac":binding[1],"commands":sorted(commands),"expires_in":60}
        if path=="/private/v1/devices" and method=="GET":
            self.access.require(grant,"manage","user")
            return 200,self.store.list_devices(p)
        if path=="/stackChan/v2/device/update" and method=="PUT":
            self.access.require(grant,"manage","user")
            if set(data)!={"mac","name"}:return 501,None # location support is not invented
            self.store.rename(p,data["mac"],data["name"]);return 200,True
        if path=="/stackChan/v2/device/unbind" and method=="POST":
            self.access.require(grant,"manage","user")
            self.memory.purge(p,data["mac"])
            self.dances.purge(p,data["mac"])
            result=self.store.unbind(p,data["mac"])
            await self.disconnect_device(mac(data["mac"]))
            return 200,result
        if path in ("/stackChan/device/info","/stackChan/device/user") and method=="GET":
            self.access.require(grant,"device","device")
            owner,address,_=self.binding(grant)
            row=self.store.owned(Principal(owner,"user"),address)[1]
            return 200,{"name":row[1]} if path.endswith("info") else {"username":owner}
        if path=="/stackChan/device/unbind" and method=="POST":
            self.access.require(grant,"device","device")
            owner,address,_=self.binding(grant);user=Principal(owner,"user")
            self.memory.purge(user,address);self.dances.purge(user,address);result=self.store.unbind(user,address)
            await self.disconnect_device(address)
            return 200,result
        if path.startswith("/stackChan/") or path.startswith("/api/"):
            return 501,{"reason":"route not implemented; no vendor fallback"}
        return 404,None

    async def disconnect_device(self,address):
        # Revoke even unbound claim grants, before the first await.
        self.identity.invalidate(address)
        self.claims.pending.pop(address,None)
        self.pairing.revoke(address)
        self.control_permissions = {p for p in self.control_permissions if p[1] != address}
        self.tools.permissions = {p for p in self.tools.permissions if p[1] != address}
        peers=[self.control_devices.get(address),*self.control_apps.get(address,set())]
        for peer in peers:
            if peer:
                with contextlib.suppress(Exception):await peer.close(1008)
        # Voice sockets are revoked by the per-connection watchdog and per-emission check.

    async def websocket(self,ws,headers,scope,binding,grant):
        address=binding[1]
        voice=None
        if scope=="voice":
            voice_class=PcmVoice if self.codec is not None else AsyncVoice
            options={"codec":self.codec,"allow_realtime":self.allow_realtime} if self.codec is not None else {}
            voice=voice_class(secrets.token_hex(16),lambda:self.check(headers,scope,binding),ws.send,self.provider,self.privacy,**options)
        if scope=="control":
            if grant.principal.kind=="device":
                old=self.control_devices.get(address)
                if old:
                    await self.hub.detach(old,address,True)
                    await old.close(1000)
                self.control_devices[address]=ws
            else:self.control_apps.setdefault(address,set()).add(ws)
        async def watchdog():
            next_heartbeat=asyncio.get_running_loop().time()+self.heartbeat_interval
            while not ws.closed:
                await asyncio.sleep(.05)
                try:
                    self.check(headers,scope,binding)
                    if scope=="control" and grant.principal.kind=="user":self.hub.check_active(ws,binding)
                    if scope=="control" and asyncio.get_running_loop().time()>=next_heartbeat:
                        await ws.send(encode(0x10))
                        next_heartbeat=asyncio.get_running_loop().time()+self.heartbeat_interval
                except Denied:
                    await ws.close(1008);return
        watch=asyncio.create_task(watchdog())
        try:
            while not ws.closed:
                message=await asyncio.wait_for(ws.receive(),120)
                if message is None:break
                self.check(headers,scope,binding)
                if voice:
                    await voice.receive(message)
                else:
                    if grant.principal.kind=="device" and message=={"type":"hello","msg":"Hello from StackChan!"}:
                        continue
                    if grant.principal.kind=="device" and isinstance(message,str) and message in ("camera stream started","camera stream stopped"):
                        continue  # Exact upstream acknowledgments; never forwarded or treated as commands.
                    if not isinstance(message,bytes):raise ValueError("control is binary")
                    kind,_=decode(message)
                    if kind==0x10:await ws.send(encode(0x11));continue
                    if kind==0x11:continue
                    await self.hub.receive(ws,message,binding,grant.principal.kind=="device")
        finally:
            watch.cancel()
            await asyncio.gather(watch,return_exceptions=True)
            if voice:await voice.close()
            if scope=="control":await self.hub.detach(ws,address,grant.principal.kind=="device")
            self.control_apps.get(address,set()).discard(ws)

async def main():
    import argparse
    parser=argparse.ArgumentParser(description="Loopback development service; no account bootstrap, no real provider")
    parser.add_argument("--port",type=int,default=12800)
    parser.add_argument("--db",default=":memory:")
    args=parser.parse_args()
    service=await Service(args.db).start(args.port)
    print(f"Development listener: http://127.0.0.1:{service.port}; proof endpoints require a trusted registered verifier; no account bootstrap")
    try:await asyncio.Event().wait()
    finally:await service.close()

if __name__=="__main__":
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
