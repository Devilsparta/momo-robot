"""Device proof exchange. Trusted registration supplies a verifier, never HTTP keys.

No credential creation/storage or algorithm selection. Production provisioning must
install the same key ID and signing/verifying implementation on both peers.
"""
import re
import secrets
import time
from .device_store import Principal, Denied, mac


class Identity:
    def __init__(self, store, access, clock=time.time):
        self.store, self.access, self.clock = store, access, clock
        self.verifiers, self.pending, self.attempts = {}, {}, {}

    def register(self, address, key_id, verify):
        address = mac(address)
        if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id) or not callable(verify):
            raise ValueError("invalid trusted verifier")
        if address not in self.verifiers and len(self.verifiers) >= 1024:
            raise Denied("identity capacity")
        self.invalidate(address)
        self.verifiers[address] = (key_id, verify)

    def invalidate(self, address):
        address = mac(address)
        self.pending = {k:v for k,v in self.pending.items() if v[0] != address}
        self.access.revoke_device(address)

    def revoke(self, address):
        address = mac(address)
        self.verifiers.pop(address, None)
        self.invalidate(address)

    def epoch(self, address):
        return self.store.db.execute("SELECT owner,epoch FROM devices WHERE mac=?", (address,)).fetchone()

    def challenge(self, address):
        address, now = mac(address), self.clock()
        self.pending = {k:v for k,v in self.pending.items() if v[3] > now}
        self.attempts = {k:v for k,v in self.attempts.items() if v[1] > now}
        if address not in self.verifiers: raise Denied("identity unavailable")
        count, until = self.attempts.get(address, (0, now+60))
        if count >= 5 or len(self.pending) >= 128: raise Denied("challenge budget")
        self.attempts[address] = (count+1, until)
        key_id, verify = self.verifiers[address]
        ident, nonce = secrets.token_hex(16), secrets.token_hex(32)
        message = f"stackchan-device-session-v1\n{address}\n{key_id}\n{ident}\n{nonce}"
        # The row snapshot prevents proofs crossing bind, unbind, or reassignment.
        self.pending[ident] = (address, self.verifiers[address], message.encode("ascii"), now+60, self.epoch(address))
        return {"mac":address, "key_id":key_id, "challenge_id":ident, "nonce":nonce, "expires_in":60}

    def prove(self, ident, proof):
        if not isinstance(ident, str) or not isinstance(proof, str) or not 1 <= len(proof) <= 4096:
            raise ValueError("invalid proof")
        item = self.pending.pop(ident, None)  # Every proof attempt consumes its challenge.
        if item is None: raise Denied("challenge unavailable")
        address, registration, message, until, epoch = item
        if until <= self.clock() or self.verifiers.get(address) is not registration or self.epoch(address) != epoch:
            raise Denied("challenge invalidated")
        try:
            valid = registration[1](message, proof) is True
        except Exception:
            valid = False
        if not valid: raise Denied("proof rejected")
        binding = (epoch[0], address, epoch[1]) if epoch and epoch[0] is not None else None
        scopes = {"device", "voice", "control"} if binding else {"claim"}
        token = self.access.issue(Principal(address,"device"), scopes, ttl=300, binding=binding)
        return {"state":"bound" if binding else "unbound", "device_token":token, "expires_in":300}
