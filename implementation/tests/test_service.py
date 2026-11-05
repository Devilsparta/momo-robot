"""Real loopback TCP HTTP/WS integration tests. All accounts/audio are synthetic."""
import asyncio
import base64
import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from backend.service import Service
from backend.device_store import Principal,Denied
from backend.voice import Privacy
from backend.control import encode
from backend.async_voice import AsyncVoice

MAC="020000000001"
OTHER="020000000002"
HELLO={"type":"hello","version":1,"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}

class Client:
    def __init__(self,reader,writer):
        self.r,self.w=reader,writer

    async def send(self,value,opcode=None,masked=True):
        if opcode is None:opcode=2 if isinstance(value,bytes) else 1
        body=value if isinstance(value,bytes) else json.dumps(value).encode()
        n=len(body);mask=os.urandom(4)
        header=bytes([0x80|opcode,(0x80 if masked else 0)|(n if n<126 else 126 if n<65536 else 127)])
        if n>=65536:header+=struct.pack(">Q",n)
        elif n>=126:header+=struct.pack(">H",n)
        if masked:header+=mask;body=bytes(v^mask[i%4] for i,v in enumerate(body))
        self.w.write(header+body);await self.w.drain()

    async def recv(self):
        async def read():
            a,b=await self.r.readexactly(2)
            assert a&0x80 and not b&0x80
            size=b&127
            if size==126:size=struct.unpack(">H",await self.r.readexactly(2))[0]
            elif size==127:size=struct.unpack(">Q",await self.r.readexactly(8))[0]
            body=await self.r.readexactly(size)
            if a&15==8:return ("close",struct.unpack(">H",body[:2])[0])
            if a&15==10:return ("pong",body)
            return body if a&15==2 else json.loads(body)
        return await asyncio.wait_for(read(),2)

    async def close(self):
        self.w.close()
        await self.w.wait_closed()

class Integration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s=await Service().start()
        self.addAsyncCleanup(self.s.close)
        self.a=Principal("synthetic-alice","user")
        self.b=Principal("synthetic-bob","user")
        self.at=self.s.access.issue(self.a,{"manage","control","tools"})
        self.bt=self.s.access.issue(self.b,{"manage","control","tools"})
        self.provision=self.s.access.issue(Principal(MAC,"device"),{"claim"})
        self.peers=[]

    async def asyncTearDown(self):
        for p in self.peers:await p.close()

    async def http(self,path,method="GET",body=None,token=None,extra=""):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.s.port)
        raw=json.dumps(body).encode() if body is not None else b""
        auth=f"Authorization: Bearer {token}\r\n" if token else ""
        writer.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.s.port}\r\n{auth}Content-Length: {len(raw)}\r\n{extra}\r\n".encode()+raw)
        await writer.drain()
        result=await asyncio.wait_for(reader.read(),2)
        writer.close();await writer.wait_closed()
        head,data=result.split(b"\r\n\r\n",1)
        return int(head.split(b" ")[1]),json.loads(data)

    async def bind(self):
        status,result=await self.http("/private/v1/claim","POST",{},self.provision)
        self.assertEqual(status,200)
        code=result["data"]["code"]
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":code},self.at))[0],200)
        binding=self.s.store.session(self.a,MAC)
        return self.s.access.issue(Principal(MAC,"device"),{"device","voice","control"},binding=binding)

    async def ws(self,path,token,headers=""):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.s.port)
        key=base64.b64encode(os.urandom(16)).decode()
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.s.port}\r\nAuthorization: Bearer {token}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n{headers}\r\n".encode())
        await writer.drain()
        head=await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),2)
        if not head.startswith(b"HTTP/1.1 101"):
            writer.close();await writer.wait_closed()
            return int(head.split(b" ")[1])
        expected=base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        self.assertIn(expected,head)
        client=Client(reader,writer);self.peers.append(client)
        return client

    async def test_firmware_greeting_and_server_heartbeat(self):
        token=await self.bind()
        self.s.heartbeat_interval=.03
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        await device.send({"type":"hello","msg":"Hello from StackChan!"})
        self.assertEqual(await device.recv(),encode(0x10))
        await device.send(encode(0x11))
        self.s.heartbeat_interval=3
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        frame=encode(3,b"{}")
        await app.send(encode(3,MAC.encode()+b"{}"))
        received=await device.recv()
        if received==encode(0x10):received=await device.recv()
        self.assertEqual(received,frame)
        self.assertEqual({p[2] for p in self.s.control_permissions},{3,4})

    async def test_http_claim_info_config_and_isolation(self):
        token=await self.bind()
        self.assertEqual((await self.http("/health"))[0],200)
        self.assertEqual((await self.http("/stackChan/device/info",token=token))[1]["data"],{"name":""})
        self.assertEqual((await self.http("/private/v1/config","PUT",{"mac":MAC,"config":{"volume":30}},self.at))[0],200)
        self.assertEqual((await self.http("/private/v1/config?mac="+MAC,token=self.bt))[0],403)
        self.assertEqual((await self.http("/private/v1/config?mac="+MAC,token=self.at))[1]["data"],{"volume":30})
        self.assertEqual((await self.http("/stackChan/v2/device/activate_bind","POST",{"mac":OTHER},self.at))[0],501)

    async def test_claim_race_only_one_owner(self):
        _,result=await self.http("/private/v1/claim","POST",{},self.provision)
        body={"mac":MAC,"code":result["data"]["code"]}
        results=await asyncio.gather(self.http("/private/v1/bind","POST",body,self.at),self.http("/private/v1/bind","POST",body,self.bt))
        self.assertEqual(sorted(x[0] for x in results),[200,403])

    async def test_claim_replay_and_unknown_identity(self):
        _,result=await self.http("/private/v1/claim","POST",{},self.provision)
        body={"mac":MAC,"code":result["data"]["code"]}
        self.assertEqual((await self.http("/private/v1/bind","POST",body,self.at))[0],200)
        self.assertEqual((await self.http("/private/v1/bind","POST",body,self.at))[0],403)
        self.assertEqual((await self.http("/private/v1/devices",token="invented"))[0],403)

    async def test_memory_opt_in_delete_and_unbind_purge(self):
        await self.bind()
        payload={"mac":MAC,"text":"synthetic note"}
        self.assertEqual((await self.http("/private/v1/memory","POST",payload,self.at))[0],403)
        await self.http("/private/v1/memory/consent","PUT",{"mac":MAC,"enabled":True},self.at)
        _,result=await self.http("/private/v1/memory","POST",payload,self.at)
        ident=result["data"]["id"]
        self.assertEqual((await self.http("/private/v1/memory?mac="+MAC,token=self.bt))[0],403)
        self.assertEqual(len((await self.http("/private/v1/memory?mac="+MAC,token=self.at))[1]["data"]),1)
        await self.http("/private/v1/memory","DELETE",{"mac":MAC,"id":ident},self.at)
        self.assertEqual((await self.http("/private/v1/memory?mac="+MAC,token=self.at))[1]["data"],[])
        await self.http("/private/v1/memory","POST",payload,self.at)
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memory_notes").fetchone()[0],0)

    async def test_control_forward_reconnect_and_revoke(self):
        token=await self.bind()
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        self.s.control_permissions.add((self.a.subject,MAC,3))
        await app.send(encode(3,MAC.encode()+b"{}"))
        self.assertEqual(await device.recv(),encode(3,b"{}"))
        new=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        self.assertEqual(await device.recv(),("close",1000))
        await app.send(encode(3,MAC.encode()+b"[]"))
        self.assertEqual(await new.recv(),encode(3,b"[]"))
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual(await app.recv(),("close",1008))
        self.assertEqual(await new.recv(),("close",1008))
        self.assertEqual(await self.ws("/stackChan/ws?deviceType=StackChan",token),403)
        self.assertFalse(any(p[1]==MAC for p in self.s.control_permissions))
        self.assertFalse(any(p[1]==MAC for p in self.s.tools.permissions))

    async def test_cross_owner_and_camera_default_denied(self):
        token=await self.bind()
        self.assertEqual(await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.bt),403)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await app.send(encode(5,MAC.encode()))
        self.assertEqual(await app.recv(),("close",1008))

    async def test_voice_real_ws_hello_abort_and_revocation(self):
        token=await self.bind()
        voice=await self.ws("/xiaozhi/v1/",token,"Protocol-Version: 1\r\n")
        await voice.send(HELLO);reply=await voice.recv()
        ident=reply["session_id"];self.assertEqual(reply["type"],"hello")
        await voice.send({"type":"listen","state":"start","mode":"manual","session_id":ident})
        await voice.send(b"synthetic-audio")
        await voice.send({"type":"abort","session_id":ident})
        await voice.send(b"x",opcode=9)
        self.assertEqual(await voice.recv(),("pong",b"x"))
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual(await voice.recv(),("close",1008))

    async def test_invalid_frame_close_and_malformed_http(self):
        token=await self.bind()
        ws=await self.ws("/xiaozhi/v1/",token)
        await ws.send(HELLO,masked=False)
        self.assertEqual(await ws.recv(),("close",1002))
        self.assertEqual((await self.http("/health",extra="Content-Length: 0\r\n"))[0],400)
        self.assertEqual((await self.http("/health",extra="Origin: https://foreign.example\r\n"))[0],400)

    async def test_tool_scope_arguments_timeout_and_revoke(self):
        await self.bind()
        async def echo(args):return {"value":args["value"]}
        self.s.tools.register("echo",echo,lambda a:set(a)=={"value"} and type(a["value"]) is int)
        body={"mac":MAC,"name":"echo","arguments":{"value":2}}
        self.assertEqual((await self.http("/private/v1/tools/call","POST",body,self.at))[0],403)
        self.s.tools.permissions.add((self.a.subject,MAC,"echo"))
        self.assertEqual((await self.http("/private/v1/tools/call","POST",body,self.at))[1]["data"],{"value":2})
        body["arguments"]={"value":"bad"}
        self.assertEqual((await self.http("/private/v1/tools/call","POST",body,self.at))[0],400)

    async def test_loopback_only(self):
        with self.assertRaises(ValueError):await self.s.start(host="0.0.0.0")

    async def test_expired_grant_revokes_idle_socket(self):
        token=await self.bind()
        ws=await self.ws("/xiaozhi/v1/",token)
        await ws.send(HELLO);await ws.recv()
        self.s.access.clock=lambda:10**12
        self.assertEqual(await ws.recv(),("close",1008))

    async def test_scope_cannot_be_upgraded_by_headers(self):
        await self.bind()
        self.assertEqual(await self.ws("/xiaozhi/v1/",self.provision,"Device-Id: "+MAC+"\r\n"),403)
        limited=self.s.access.issue(self.a,{"manage"})
        self.assertEqual(await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,limited),403)

    async def test_claim_attempt_budget_and_expiration(self):
        for i in range(5):
            self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":"wrong"},self.at))[0],403)
        _,result=await self.http("/private/v1/claim","POST",{},self.provision)
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":result["data"]["code"]},self.at))[0],403)
        with self.assertRaises(Denied):
            self.s.claims.verify(self.b.subject,MAC,result["data"]["code"])
        self.s.claims.clock=lambda:10**12
        with self.assertRaises(Denied):
            self.s.claims.verify(self.b.subject,MAC,result["data"]["code"])

    async def test_voice_tcp_provider_cancel_drops_late_output(self):
        entered=asyncio.Event()
        class Provider:
            egress_categories=frozenset({"audio"})
            async def respond(self,frames):
                yield b"synthetic-packet"
                entered.set()
                try:await asyncio.sleep(10)
                except asyncio.CancelledError:yield b"late-packet"
        self.s.provider=Provider()
        self.s.privacy=Privacy(audio_egress=True)
        token=await self.bind()
        ws=await self.ws("/xiaozhi/v1/",token)
        await ws.send(HELLO);ident=(await ws.recv())["session_id"]
        await ws.send({"type":"listen","state":"start","mode":"manual","session_id":ident})
        await ws.send(b"synthetic-input")
        await ws.send({"type":"listen","state":"stop","session_id":ident})
        self.assertEqual((await ws.recv())["state"],"start")
        self.assertEqual(await ws.recv(),b"synthetic-packet")
        await asyncio.wait_for(entered.wait(),1)
        await ws.send({"type":"abort","session_id":ident})
        self.assertEqual((await ws.recv())["state"],"stop")
        await ws.send(b"barrier",opcode=9)
        self.assertEqual(await ws.recv(),("pong",b"barrier"))

    async def test_real_tool_timeout_and_unbind_during_call(self):
        await self.bind()
        entered=asyncio.Event()
        async def slow(args):
            entered.set()
            await asyncio.sleep(1)
            return {"done":True}
        self.s.tools.register("slow",slow,lambda _:True)
        self.s.tools.permissions.add((self.a.subject,MAC,"slow"))
        self.s.tools.timeout=.01
        result=await self.http("/private/v1/tools/call","POST",{"mac":MAC,"name":"slow","arguments":{}},self.at)
        # Service reports a bounded timeout, never a fabricated successful tool result.
        self.assertEqual(result[0],409)
        entered.clear()
        self.s.tools.timeout=2
        pending=asyncio.create_task(self.http("/private/v1/tools/call","POST",{"mac":MAC,"name":"slow","arguments":{}},self.at))
        await asyncio.wait_for(entered.wait(),1)
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual((await pending)[0],403)

    async def test_memory_opt_out_and_payload_validation(self):
        await self.bind()
        await self.http("/private/v1/memory/consent","PUT",{"mac":MAC,"enabled":True},self.at)
        self.assertEqual((await self.http("/private/v1/memory","POST",{"mac":MAC,"text":"x"*4097},self.at))[0],400)
        await self.http("/private/v1/memory","POST",{"mac":MAC,"text":"synthetic"},self.at)
        await self.http("/private/v1/memory/consent","PUT",{"mac":MAC,"enabled":False},self.at)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memory_notes").fetchone()[0],0)
        self.assertEqual((await self.http("/private/v1/memory?mac="+MAC,token=self.at))[0],403)

    async def test_ota_does_not_trust_device_id_or_invent_upgrade(self):
        token=await self.bind()
        self.assertEqual((await self.http("/xiaozhi/ota/","POST",{"application":{"version":"1.6.0"}},extra="Device-Id: "+MAC+"\r\n"))[0],403)
        result=await self.http("/xiaozhi/ota/","POST",{"application":{"version":"1.6.0"}},token,extra="Device-Id: "+MAC+"\r\n")
        self.assertEqual(result[0],200)
        self.assertEqual(result[1]["firmware"],{"version":"1.6.0"})
        self.assertEqual(result[1]["websocket"],{"version":1})
        self.assertNotIn("data",result[1])
        self.assertEqual((await self.http("/xiaozhi/ota/","POST",{"application":{"version":"1.6.0"}},token,extra="Device-Id: "+OTHER+"\r\n"))[0],403)

    async def test_unbind_revokes_claim_grants_pending_codes_and_name(self):
        await self.bind()
        await self.http("/stackChan/v2/device/update","PUT",{"mac":MAC,"name":"private name"},self.at)
        pending={"data":{"code":self.s.claims.pending[MAC][0]}}
        self.assertEqual((await self.http("/private/v1/claim","POST",{},self.provision))[0],403)
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual((await self.http("/private/v1/claim","POST",{},self.provision))[0],403)
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":pending["data"]["code"]},self.bt))[0],403)
        self.assertEqual(self.s.db.execute("SELECT name FROM devices WHERE mac=?",(MAC,)).fetchone()[0],"")

    async def test_tool_inflight_permission_grant_and_expiry_revocation(self):
        await self.bind()
        entered=asyncio.Event();release=asyncio.Event()
        async def paused(args):
            entered.set();await release.wait();return {"secret":"must not return"}
        self.s.tools.register("paused",paused,lambda _:True)
        permission=(self.a.subject,MAC,"paused")
        for reason in ("permission","grant","expiry"):
            entered.clear();release.clear()
            self.s.access.clock=__import__("time").time
            token=self.s.access.issue(self.a,{"tools"})
            self.s.tools.permissions.add(permission)
            pending=asyncio.create_task(self.http("/private/v1/tools/call","POST",{"mac":MAC,"name":"paused","arguments":{}},token))
            await asyncio.wait_for(entered.wait(),1)
            if reason=="permission":self.s.tools.permissions.discard(permission)
            elif reason=="grant":self.s.access.grants.pop(hashlib.sha256(token.encode()).digest())
            else:self.s.access.clock=lambda:10**12
            release.set()
            status,result=await pending
            self.assertEqual(status,403,reason);self.assertNotIn("secret",json.dumps(result))

    async def test_unimplemented_media_cannot_be_enabled_by_generic_permission(self):
        token=await self.bind()
        for kind in (1,2,5,7,9,0x12,0x18,0x1b):
            app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
            self.s.control_permissions.add((self.a.subject,MAC,kind))
            await app.send(encode(kind,MAC.encode()))
            self.assertEqual(await app.recv(),("close",1008))
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        self.s.control_permissions.add((self.a.subject,MAC,2))
        await device.send(encode(2,b"synthetic-image"))
        self.assertEqual(await device.recv(),("close",1008))

    async def test_control_queued_output_rechecks_before_write(self):
        token=await self.bind()
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        peer=self.s.control_devices[MAC]
        await peer.lock.acquire()
        permission=(self.a.subject,MAC,3)
        self.s.control_permissions.add(permission)
        await app.send(encode(3,MAC.encode()+b"pending"))
        await asyncio.sleep(.02)
        self.s.control_permissions.discard(permission)
        peer.lock.release()
        self.assertEqual(await app.recv(),("close",1008))
        await device.send(encode(0x10))
        self.assertEqual(await device.recv(),encode(0x11))

    async def test_pairing_display_confirm_poll_and_rebind(self):
        _,start=await self.http("/private/v1/claim","POST",{},self.provision)
        claim=start["data"]; path="/private/v1/claim/status?claim_id="+claim["claim_id"]
        self.assertRegex(claim["code"],r"^[0-9]{6}$")
        self.assertEqual((await self.http(path,token=self.provision))[1]["data"]["state"],"waiting")
        same_device_other_grant=self.s.access.issue(Principal(MAC,"device"),{"claim"})
        self.assertEqual((await self.http(path,token=same_device_other_grant))[0],403)
        self.assertEqual((await self.http(path,token=self.at))[0],403)
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":claim["code"]},self.at))[0],200)
        _,poll=await self.http(path,token=self.provision);bound=poll["data"]
        self.assertEqual(bound["state"],"bound");token=bound["device_token"]
        self.assertEqual((await self.http(path,token=self.provision))[1]["data"]["device_token"],token)
        self.assertEqual((await self.http("/stackChan/device/user",token=token))[1]["data"],{"username":self.a.subject})
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":claim["code"]},self.bt))[0],403)
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual((await self.http(path,token=self.provision))[0],403)
        self.assertEqual((await self.http("/stackChan/device/info",token=token))[0],403)
        new_provision=self.s.access.issue(Principal(MAC,"device"),{"claim"})
        _,new=await self.http("/private/v1/claim","POST",{},new_provision)
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":new["data"]["code"]},self.bt))[0],200)
        self.assertEqual(self.s.store.list_devices(self.a),[])
        self.assertEqual(len(self.s.store.list_devices(self.b)),1)

    async def test_pairing_cancel_invalidates_display_code(self):
        _,result=await self.http("/private/v1/claim","POST",{},self.provision)
        claim=result["data"]
        self.assertEqual((await self.http("/private/v1/claim","DELETE",{"claim_id":claim["claim_id"]},self.at))[0],403)
        self.assertEqual((await self.http("/private/v1/claim","DELETE",{"claim_id":claim["claim_id"]},self.provision))[0],200)
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":claim["code"]},self.at))[0],403)

    async def test_pairing_rotation_and_expiry(self):
        _,first=await self.http("/private/v1/claim","POST",{},self.provision)
        _,second=await self.http("/private/v1/claim","POST",{},self.provision)
        self.assertEqual((await self.http("/private/v1/claim/status?claim_id="+first["data"]["claim_id"],token=self.provision))[0],403)
        self.s.claims.clock=lambda:10**12
        self.assertEqual((await self.http("/private/v1/bind","POST",{"mac":MAC,"code":second["data"]["code"]},self.at))[0],403)
        self.assertEqual((await self.http("/private/v1/claim/status?claim_id="+second["data"]["claim_id"],token=self.provision))[0],403)

    async def test_identity_proof_replay_bind_epoch_and_renewal(self):
        import hmac
        # A public synthetic fixture, not a generated/configured device credential.
        secret=b"synthetic-unit-test-key-only"
        def proof(challenge):
            d=challenge["data"]
            message="\n".join(("stackchan-device-session-v1",MAC,d["key_id"],d["challenge_id"],d["nonce"]))
            return {"challenge_id":d["challenge_id"],"proof":hmac.new(secret,message.encode(),"sha256").hexdigest()}
        self.s.identity.register(MAC,"synthetic",lambda m,p:hmac.compare_digest(hmac.new(secret,m,"sha256").hexdigest(),p))
        self.assertEqual((await self.http("/private/v1/identity/challenge","POST",{"mac":OTHER}))[0],403)
        _,challenge=await self.http("/private/v1/identity/challenge","POST",{"mac":MAC})
        body=proof(challenge)
        status,result=await self.http("/private/v1/identity/prove","POST",body)
        self.assertEqual(status,200);self.assertEqual(result["data"]["state"],"unbound")
        self.assertEqual((await self.http("/private/v1/identity/prove","POST",body))[0],403)
        self.provision=result["data"]["device_token"]
        _,stale=await self.http("/private/v1/identity/challenge","POST",{"mac":MAC})
        await self.bind()
        self.assertEqual((await self.http("/private/v1/identity/prove","POST",proof(stale)))[0],403)
        _,challenge=await self.http("/private/v1/identity/challenge","POST",{"mac":MAC})
        _,renewed=await self.http("/private/v1/identity/prove","POST",proof(challenge))
        token=renewed["data"]["device_token"]
        self.assertEqual(renewed["data"]["state"],"bound")
        self.assertEqual((await self.http("/stackChan/device/info",token=token))[0],200)
        _,stale=await self.http("/private/v1/identity/challenge","POST",{"mac":MAC})
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual((await self.http("/private/v1/identity/prove","POST",proof(stale)))[0],403)
        self.assertEqual((await self.http("/stackChan/device/info",token=token))[0],403)

    async def test_name_posture_dance_match_upstream_frames(self):
        token=await self.bind()
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await self.http("/stackChan/v2/device/update","PUT",{"mac":MAC,"name":"Robot"},self.at)
        await app.send(encode(14));self.assertEqual(await app.recv(),encode(14,b"Robot"))
        await app.send(encode(21));self.assertEqual(await device.recv(),encode(21))
        await device.send(encode(21,b"synthetic-posture"));self.assertEqual(await app.recv(),encode(21,b"synthetic-posture"))
        await app.send(encode(20,b"synthetic-dance"));self.assertEqual(await device.recv(),encode(20,b"synthetic-dance"))
        await app.send(encode(13,b"untrusted-name"))
        self.assertEqual(await device.recv(),encode(13,b"Robot"));self.assertEqual(await app.recv(),encode(13,b"Robot"))

    async def test_media_subscription_refcounts_revoke_and_reconnect(self):
        token=await self.bind()
        self.s.hub.policy.camera=True  # Synthetic frames only on 127.0.0.1.
        self.s.control_permissions.add((self.a.subject,MAC,5))
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        second=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await app.send(encode(5,MAC.encode()));self.assertEqual(await device.recv(),encode(5,MAC.encode()))
        await second.send(encode(5,MAC.encode()))
        await second.send(b"barrier",opcode=9);await second.recv()
        await device.send(b"camera stream started",opcode=1)
        await device.send(encode(2,b"synthetic-frame"))
        self.assertEqual(await app.recv(),encode(2,b"synthetic-frame"));self.assertEqual(await second.recv(),encode(2,b"synthetic-frame"))
        large=encode(2,b"x"*70000)
        await device.send(large)
        self.assertEqual(await app.recv(),large);self.assertEqual(await second.recv(),large)
        await app.send(encode(6,MAC.encode()));await app.send(b"barrier",opcode=9);await app.recv()
        self.s.control_permissions.discard((self.a.subject,MAC,5))
        self.assertEqual(await second.recv(),("close",1008))
        self.assertEqual(await device.recv(),encode(6))
        self.s.control_permissions.add((self.a.subject,MAC,5))
        await app.send(encode(5,MAC.encode()));self.assertEqual(await device.recv(),encode(5,MAC.encode()))
        await self.ws("/stackChan/ws?deviceType=StackChan",token)
        self.assertEqual(await app.recv(),("close",1001));self.assertEqual(await device.recv(),("close",1000))
        self.assertFalse(self.s.hub.subs.get((MAC,5)))

    async def test_private_dance_crud_matches_app_and_owner_isolation(self):
        await self.bind()
        frame={"leftEye":{},"rightEye":{},"mouth":{},"yawServo":{"angle":0,"speed":500},
               "pitchServo":{"angle":0,"speed":500},"durationMs":1000,"leftRgbColor":"#000000","rightRgbColor":"#000000"}
        body={"mac":MAC,"danceName":"Synthetic dance","danceData":[frame],"musicUrl":""}
        self.assertEqual((await self.http("/stackChan/v2/dance","POST",body,self.bt))[0],403)
        result=await self.http("/stackChan/v2/dance","POST",body,self.at)
        self.assertEqual(result[0],200);self.assertIsInstance(result[1]["data"],str)
        self.assertEqual((await self.http("/stackChan/v2/dance","POST",body,self.at))[0],403)
        _,result=await self.http("/stackChan/v2/dance?mac="+MAC,token=self.at)
        saved=result["data"][0];ident=saved["id"]
        self.assertEqual(saved["danceData"],[frame]);self.assertEqual(saved["musicUrl"],"")
        self.assertEqual((await self.http("/stackChan/dance?id="+str(ident),token=self.at))[1]["data"],saved)
        self.assertEqual((await self.http("/stackChan/dance?id="+str(ident),token=self.bt))[0],403)
        update={"id":ident,"danceName":"Updated","danceData":[dict(frame,durationMs=2000)],"musicUrl":""}
        self.assertEqual((await self.http("/stackChan/v2/dance","PUT",update,self.at))[0],200)
        self.assertEqual((await self.http("/stackChan/v2/dance","DELETE",{"id":ident},self.bt))[0],403)
        self.assertEqual((await self.http("/stackChan/v2/dance","DELETE",{"id":ident},self.at))[0],200)
        await self.http("/stackChan/v2/dance","POST",body,self.at)
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM private_dances").fetchone()[0],0)

    async def test_private_dance_external_music_and_malformed_frames_rejected(self):
        await self.bind()
        body={"mac":MAC,"danceName":"Synthetic","danceData":[],"musicUrl":"https://media.example.invalid/song.mp3"}
        self.assertEqual((await self.http("/stackChan/v2/dance","POST",body,self.at))[0],403)
        body["musicUrl"]=""
        self.assertEqual((await self.http("/stackChan/v2/dance","POST",body,self.at))[0],400)
        body["danceData"]=[{"durationMs":1000}]
        self.assertEqual((await self.http("/stackChan/v2/dance","POST",body,self.at))[0],400)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM private_dances").fetchone()[0],0)

    async def test_capabilities_default_and_screen_photo_isolation(self):
        token=await self.bind()
        result=await self.http("/private/v1/capabilities?mac="+MAC,token=self.at)
        self.assertEqual(result[0],200)
        self.assertTrue({3,4,13,14,17,20,21}.issubset(result[1]["data"]["commands"]))
        self.assertFalse({5,9,18,24,26}&set(result[1]["data"]["commands"]))
        self.assertEqual((await self.http("/private/v1/capabilities?mac="+MAC,token=self.bt))[0],403)
        self.s.hub.policy.camera=self.s.hub.policy.phone_screen=True
        self.s.control_permissions.update((self.a.subject,MAC,k) for k in (18,26))
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        other=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await app.send(encode(18,MAC.encode()));self.assertEqual(await device.recv(),encode(18,MAC.encode()))
        await other.send(encode(2,MAC.encode()+b"unauthorized-screen"));self.assertEqual(await other.recv(),("close",1008))
        await app.send(encode(2,MAC.encode()+b"synthetic-screen"));self.assertEqual(await device.recv(),encode(2,b"synthetic-screen"))
        await app.send(encode(26,b"synthetic-request"));self.assertEqual(await device.recv(),encode(26,b"synthetic-request"))
        await device.send(encode(26,b"synthetic-photo"));self.assertEqual(await app.recv(),encode(26,b"synthetic-photo"))
        await app.close();self.assertEqual(await device.recv(),encode(19))
        await device.send(encode(14,b"local-name"));self.assertEqual(await device.recv(),encode(13,b""))

    async def test_media_activation_rechecks_queued_permission(self):
        token=await self.bind()
        self.s.hub.policy.camera=True
        permission=(self.a.subject,MAC,5);self.s.control_permissions.add(permission)
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        server_device=self.s.control_devices[MAC]
        await server_device.lock.acquire()
        try:
            await app.send(encode(5,MAC.encode()))
            for _ in range(30):
                if self.s.hub.subs.get((MAC,5)):break
                await asyncio.sleep(.005)
            self.assertTrue(self.s.hub.subs.get((MAC,5)))
            self.s.control_permissions.discard(permission)
        finally:server_device.lock.release()
        self.assertEqual(await app.recv(),("close",1008))
        # A safety stop is allowed, but a queued camera start must never be emitted.
        self.assertEqual(await device.recv(),encode(6))

    async def test_call_concurrency_answer_audio_hangup(self):
        token=await self.bind()
        policy=self.s.hub.policy;policy.calls=policy.camera=policy.microphone=True
        self.s.control_permissions.add((self.a.subject,MAC,9))
        device=await self.ws("/stackChan/ws?deviceType=StackChan",token)
        app=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        second=await self.ws("/stackChan/ws?deviceType=App&mac="+MAC,self.at)
        await app.send(encode(9,MAC.encode()+b"synthetic-caller"));self.assertEqual(await device.recv(),encode(9,b"synthetic-caller"))
        await second.send(encode(9,MAC.encode()));self.assertEqual((await second.recv())[0],15)
        await device.send(encode(11))
        self.assertEqual(await device.recv(),encode(5,MAC.encode()));self.assertEqual(await device.recv(),encode(24,MAC.encode()))
        self.assertEqual(await app.recv(),encode(11))
        await app.send(encode(1,MAC.encode()+b"synthetic-opus"));self.assertEqual(await device.recv(),encode(1,b"synthetic-opus"))
        await app.send(encode(12));self.assertEqual(await device.recv(),encode(12))
        self.assertEqual(await device.recv(),encode(6));self.assertEqual(await device.recv(),encode(25))
        await app.send(encode(1,MAC.encode()+b"late"));self.assertEqual(await app.recv(),("close",1008))


class AsyncCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_abort_stops_delayed_provider_and_drops_late_audio(self):
        started=asyncio.Event();released=asyncio.Event();output=[]
        class Provider:
            egress_categories=frozenset({"audio"})
            async def respond(self,frames):
                yield b"synthetic-first"
                started.set()
                try:await released.wait()
                except asyncio.CancelledError:yield b"synthetic-late"
        async def send(x):output.append(x)
        voice=AsyncVoice("session",lambda:None,send,Provider(),Privacy(audio_egress=True))
        self.addAsyncCleanup(voice.close)
        await voice.receive(HELLO)
        await voice.receive({"type":"listen","state":"start","mode":"manual","session_id":"session"})
        await voice.receive(b"input")
        await voice.receive({"type":"listen","state":"stop","session_id":"session"})
        await asyncio.wait_for(started.wait(),1)
        await voice.receive({"type":"abort","session_id":"session"})
        released.set()
        await asyncio.sleep(.01)
        self.assertIn(b"synthetic-first",output)
        self.assertNotIn(b"synthetic-late",output)
        self.assertEqual([x["state"] for x in output if isinstance(x,dict) and x.get("type")=="tts"],["start","stop"])

    async def test_provider_timeout_returns_sanitized_error(self):
        output=[]
        class Provider:
            egress_categories=frozenset()
            async def respond(self,frames):
                await asyncio.sleep(1)
                yield b"never"
        async def send(x):output.append(x)
        voice=AsyncVoice("s",lambda:None,send,Provider(),timeout=.01)
        self.addAsyncCleanup(voice.close)
        await voice.receive(HELLO)
        await voice.receive({"type":"listen","state":"start","mode":"manual","session_id":"s"})
        await voice.receive(b"input")
        await voice.receive({"type":"listen","state":"stop","session_id":"s"})
        await asyncio.wait_for(voice.task,1)
        self.assertEqual(output[-1]["code"],"provider_failed")

class Persistence(unittest.TestCase):
    def test_notes_survive_restart_grants_do_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"test.sqlite")
            s=Service(path);user=Principal("synthetic","user")
            claim=s.claims.start(MAC)["code"];s.store.bind(user,MAC,claim)
            s.memory.consent(user,MAC,True);s.memory.add(user,MAC,"synthetic saved")
            token=s.access.issue(user,{"manage"})
            s.db.close()
            reopened=Service(path)
            try:
                self.assertEqual(reopened.memory.export(user,MAC)[0]["text"],"synthetic saved")
                with self.assertRaises(Denied):reopened.access.authenticate({"authorization":"Bearer "+token})
            finally:reopened.db.close()
