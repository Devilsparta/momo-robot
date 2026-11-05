"""Device-display/mobile-confirm pairing over already authenticated identities.
No account creation, permanent credential provisioning or MAC authentication.
"""
import secrets
from .device_store import Denied, Principal, mac

class Pairing:
    def __init__(self, store, claims, access):
        self.store, self.claims, self.access = store, claims, access
        self.requests = {}

    def start(self, grant):
        self.access.require(grant, "claim", "device")
        address = mac(grant.principal.subject)
        row = self.store.db.execute("SELECT owner FROM devices WHERE mac=?", (address,)).fetchone()
        if row and row[0] is not None:
            raise Denied("unbind before pairing again")
        now = self.claims.clock()
        self.requests = {key: value for key, value in self.requests.items() if value["expires"] > now}
        result = self.claims.start(address)
        request_id = secrets.token_urlsafe(24)
        # Only the exact authenticated device session which displayed the code
        # may receive the resulting bound-device credential.
        self.requests[address] = {"id":request_id, "grant":grant, "expires":now+120,
                                  "owner":None, "binding":None, "token":None}
        return dict(result, claim_id=request_id)

    def confirm(self, user, address, code):
        address = mac(address)
        request = self.requests.get(address)
        if request is None or request["expires"] <= self.claims.clock():
            self.claims.verify(user.subject,address,code)
            raise Denied("pairing expired")
        self.access.require(request["grant"], "claim", "device")
        # Transaction consumes proof once and rejects concurrent/cross-owner binds.
        self.store.bind(user, address, code)
        request["owner"] = user.subject
        request["binding"] = self.store.session(user, address)
        return True

    def poll(self, grant, request_id):
        self.access.require(grant, "claim", "device")
        address = mac(grant.principal.subject)
        request = self.requests.get(address)
        if (not request or request["grant"] is not grant or request["id"] != request_id
                or request["expires"] <= self.claims.clock()):
            raise Denied("pairing unavailable")
        if request["owner"] is None:
            return {"state":"waiting", "expires_in":max(0,int(request["expires"]-self.claims.clock()))}
        self.store.check_session(request["binding"])
        if request["token"] is None:
            request["token"] = self.access.issue(Principal(address,"device"),
                {"device","control","voice"}, ttl=300, binding=request["binding"])
            request["token_expires"] = self.access.clock()+300
        if request["token_expires"] <= self.access.clock():
            raise Denied("pairing credential expired")
        return {"state":"bound", "device_token":request["token"],
                "expires_in":max(0,int(request["token_expires"]-self.access.clock()))}

    def cancel(self, grant, request_id):
        self.access.require(grant,"claim","device")
        address=mac(grant.principal.subject)
        request=self.requests.get(address)
        if not request or request["grant"] is not grant or request["id"] != request_id:
            raise Denied("pairing unavailable")
        self.claims.pending.pop(address,None)
        self.requests.pop(address,None)
        return True

    def revoke(self, address):
        self.requests.pop(mac(address),None)
