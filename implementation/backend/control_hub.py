"""Upstream Go remote frame state, restricted to the authenticated device binding.

All media policy defaults are false. Only a trusted embedding can enable policy
and owner-specific permissions. No codecs, camera capture, or external transfer.
"""
import asyncio
import contextlib
from dataclasses import dataclass
from .control import decode, encode, route
from .device_store import Principal, Denied


@dataclass
class MediaPolicy:
    camera: bool = False
    microphone: bool = False
    phone_screen: bool = False
    calls: bool = False


class ControlHub:
    def __init__(self, service):
        self.s=service
        self.policy=MediaPolicy()
        self.subs={}       # (MAC, OnCamera/OnAudio) -> app sockets
        self.calls={}      # MAC -> (app, pending/active)
        self.screens={}    # MAC -> one app sharing its screen
        self.photos={}     # MAC -> (app, monotonic deadline)
        self.locks={}

    def lock(self,address):
        return self.locks.setdefault(address,asyncio.Lock())

    def permitted(self,binding,kind):
        owner,address,_=binding
        if (owner,address,kind) not in self.s.control_permissions:return False
        return {5:self.policy.camera,24:self.policy.microphone,18:self.policy.phone_screen,
                26:self.policy.camera,9:self.policy.calls and self.policy.camera and self.policy.microphone}.get(kind,True)

    def require(self,binding,kind):
        self.s.store.check_session(binding)
        if not self.permitted(binding,kind):raise Denied("operation not permitted")

    def subscribers(self,address,kind):
        result=set(self.subs.get((address,kind),set()))
        call=self.calls.get(address)
        if call and call[1]=="active":result.add(call[0])
        return result

    async def device_send(self,address,frame,authorize=None):
        device=self.s.control_devices.get(address)
        if device is None:raise Denied("device offline")
        def check():
            if self.s.control_devices.get(address) is not device:raise Denied("device replaced")
            if authorize:authorize()
        await device.send(frame,check)

    async def transitions(self,address,before,binding=None):
        for kind,off in ((5,6),(24,25)):
            after=bool(self.subscribers(address,kind))
            if before[kind]!=after:
                def authorize():
                    if after:
                        if binding is None:raise Denied("missing media authorization")
                        for app in self.subscribers(address,kind):
                            app.authorize();self.check_active(app,binding)
                await self.device_send(address,encode(kind if after else off,address.encode() if after else b""),authorize)

    def state(self,address):
        return {kind:bool(self.subscribers(address,kind)) for kind in (5,24)}

    def check_active(self,ws,binding):
        address=binding[1]
        for kind in (5,24):
            if ws in self.subs.get((address,kind),set()):self.require(binding,kind)
        if self.screens.get(address) is ws:self.require(binding,18)
        if self.calls.get(address,(None,))[0] is ws:self.require(binding,9)
        if self.photos.get(address,(None,))[0] is ws:self.require(binding,26)

    async def receive(self,ws,frame,binding,is_device):
        address=binding[1]
        async with self.lock(address):
            ws.authorize()
            if is_device and self.s.control_devices.get(address) is not ws:raise Denied("old device socket")
            kind,payload=decode(frame)
            def authorize_kind(permission):
                def check():
                    ws.authorize();self.require(binding,permission)
                return check
            if is_device:
                if kind in (1,2):
                    subscription=24 if kind==1 else 5
                    enabled=self.policy.microphone if kind==1 else self.policy.camera
                    if not enabled:raise Denied("media disabled")
                    recipients=self.subscribers(address,subscription)
                    if not recipients:
                        await self.device_send(address,encode(25 if kind==1 else 6));return
                    for app in list(recipients):
                        def permitted(app=app):
                            ws.authorize();self.check_active(app,binding)
                            if app not in self.subscribers(address,subscription):raise Denied("subscription ended")
                        await app.send(frame,permitted)
                    return
                if kind in (10,11,12):
                    call=self.calls.get(address)
                    if not call:raise Denied("no call")
                    self.require(binding,9)
                    app,state=call;before=self.state(address)
                    if kind==11:
                        if state!="pending":raise Denied("call already answered")
                        self.calls[address]=(app,"active")
                    else:self.calls.pop(address,None)
                    await self.transitions(address,before,binding)
                    await app.send(frame,ws.authorize)
                    return
                if kind==26:
                    self.require(binding,26)
                    photo=self.photos.pop(address,None)
                    if photo and photo[1]>asyncio.get_running_loop().time():await photo[0].send(frame,authorize_kind(26))
                    return
                if kind==21:
                    for app in list(self.s.control_apps.get(address,set())):await app.send(frame,ws.authorize)
                    return
                if kind==14:
                    name=self.s.store.owned(Principal(binding[0],"user"),address)[1][1]
                    await ws.send(encode(13,name.encode("utf-8")));return
                # Device echoes of stateless commands are harmless, never forwarded as media.
                if kind in (3,4,5,6,24,25):return
                raise Denied("device operation unavailable")

            if kind==14:
                name=self.s.store.owned(Principal(binding[0],"user"),address)[1][1]
                await ws.send(encode(14,name.encode("utf-8")));return
            if kind==13:
                # HTTP update owns validation/storage; this upstream frame only notifies.
                name=self.s.store.owned(Principal(binding[0],"user"),address)[1][1]
                update=encode(13,name.encode("utf-8"))
                if address in self.s.control_devices:await self.device_send(address,update,ws.authorize)
                for app in list(self.s.control_apps.get(address,set())):await app.send(update,ws.authorize)
                return
            if kind in (3,4,20,21):
                permission=4 if kind==20 else kind
                if kind!=21:self.require(binding,permission)
                output=route(frame,binding,self.s.store,lambda _:True)
                def check():
                    ws.authorize()
                    if kind!=21:self.require(binding,permission)
                await self.device_send(address,output,check);return
            if kind in (5,6,24,25,18,19):
                if payload!=address.encode():raise Denied("wrong target")
                start=kind in (5,24,18);on={6:5,25:24,19:18}.get(kind,kind)
                if start:self.require(binding,on)
                if on==18:
                    if start:
                        if self.screens.get(address) not in (None,ws):raise Denied("screen occupied")
                        self.screens[address]=ws
                    elif self.screens.get(address) is ws:self.screens.pop(address,None)
                    else:return
                    def screen_check():
                        ws.authorize()
                        if start:self.require(binding,18)
                    await self.device_send(address,frame,screen_check);return
                before=self.state(address)
                peers=self.subs.setdefault((address,on),set())
                if start:
                    if address not in self.s.control_devices:raise Denied("device offline")
                    peers.add(ws)
                else:peers.discard(ws)
                await self.transitions(address,before,binding);return
            if kind==9:
                self.require(binding,9)
                output=route(frame,binding,self.s.store,lambda _:True)
                if address in self.calls:
                    await ws.send(encode(15,b"Device is already in a call"));return
                if address not in self.s.control_devices:raise Denied("device offline")
                self.calls[address]=(ws,"pending")
                await self.device_send(address,output,authorize_kind(9));return
            if kind==12:
                if self.calls.get(address,(None,))[0] is not ws:raise Denied("not your call")
                before=self.state(address);self.calls.pop(address,None)
                await self.device_send(address,frame,ws.authorize)
                await self.transitions(address,before,binding);return
            if kind==1:
                if self.calls.get(address)!=(ws,"active"):raise Denied("audio needs active call")
                self.require(binding,9)
                await self.device_send(address,route(frame,binding,self.s.store,lambda _:True),authorize_kind(9));return
            if kind==2:
                self.require(binding,18)
                if self.screens.get(address) is not ws:raise Denied("screen not active")
                await self.device_send(address,route(frame,binding,self.s.store,lambda _:True),authorize_kind(18));return
            if kind==26:
                self.require(binding,26)
                photo=self.photos.get(address)
                now=asyncio.get_running_loop().time()
                if photo and photo[1]>now:raise Denied("photo pending")
                self.photos[address]=(ws,now+10)
                await self.device_send(address,frame,authorize_kind(26));return
            raise Denied("operation unavailable")

    async def detach(self,ws,address,is_device):
        async with self.lock(address):
            if is_device:
                # A replaced socket must not erase the new device's state.
                if self.s.control_devices.get(address) is not ws:return
                self.s.control_devices.pop(address,None)
                active=self.subscribers(address,5)|self.subscribers(address,24)
                active.update(x[0] for x in [self.calls.get(address),self.photos.get(address)] if x)
                if address in self.screens:active.add(self.screens[address])
                for app in active:
                    with contextlib.suppress(Exception):await app.close(1001)
                self.subs.pop((address,5),None);self.subs.pop((address,24),None)
                self.calls.pop(address,None);self.screens.pop(address,None);self.photos.pop(address,None)
                return
            before=self.state(address)
            for kind in (5,24):self.subs.get((address,kind),set()).discard(ws)
            if self.calls.get(address,(None,))[0] is ws:
                self.calls.pop(address,None)
                with contextlib.suppress(Exception):await self.device_send(address,encode(12))
            if self.screens.get(address) is ws:
                self.screens.pop(address,None)
                with contextlib.suppress(Exception):await self.device_send(address,encode(19))
            if self.photos.get(address,(None,))[0] is ws:self.photos.pop(address,None)
            with contextlib.suppress(Exception):await self.transitions(address,before)
