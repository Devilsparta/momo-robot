"""Short-lived in-memory grants and replaceable device-code claim protocol.
Registration is a trusted embedding API ONLY, not exposed over HTTP.
"""
import hashlib
import secrets
import time
from dataclasses import dataclass
from .device_store import Principal, Denied, mac

@dataclass(frozen=True)
class Grant:
    principal: Principal
    scopes: frozenset
    expires: float
    binding: tuple | None = None

class Access:
    def __init__(self, store, clock=time.time):
        self.store, self.clock, self.grants = store, clock, {}

    def issue(self, principal, scopes, ttl=300, binding=None):
        if not isinstance(principal, Principal) or principal.kind not in ("user", "device"):
            raise ValueError("invalid identity")
        if not 0 < ttl <= 3600:
            raise ValueError("ephemeral grant required")
        if binding is not None:
            self.store.check_session(binding)
            if principal.kind != "device" or mac(principal.subject) != binding[1]:
                raise Denied("invalid device binding")
        self.grants = {k:v for k,v in self.grants.items() if v.expires > self.clock()}
        if len(self.grants) >= 1024: raise Denied("grant capacity reached")
        token = secrets.token_urlsafe(32)
        self.grants[hashlib.sha256(token.encode()).digest()] = Grant(principal, frozenset(scopes), self.clock()+ttl, binding)
        return token

    def authenticate(self, headers):
        value = headers.get("authorization", headers.get("token", ""))
        if not value.startswith("Bearer "):
            raise Denied("bearer authentication required")
        digest = hashlib.sha256(value[7:].encode()).digest()
        grant = self.grants.get(digest)
        if grant is None or grant.expires <= self.clock():
            self.grants.pop(digest, None)
            raise Denied("invalid or expired grant")
        if grant.binding is not None:
            self.store.check_session(grant.binding)
        return grant

    def revoke_device(self, address):
        address = mac(address)
        self.grants = {key: grant for key, grant in self.grants.items()
                       if not (grant.principal.kind == "device" and mac(grant.principal.subject) == address)}

    def require(self, grant, scope, kind=None):
        if not any(current is grant for current in self.grants.values()):
            raise Denied("grant revoked")
        if grant.expires <= self.clock() or scope not in grant.scopes or (kind and grant.principal.kind != kind):
            raise Denied("scope unavailable")
        if grant.binding is not None:
            self.store.check_session(grant.binding)

class ClaimCodes:
    def __init__(self, clock=time.time):
        self.clock, self.pending, self.attempts = clock, {}, {}

    def start(self, device):
        address = mac(device)
        self.pending = {k:v for k,v in self.pending.items() if v[2] > self.clock()}
        if len(self.pending) >= 128:
            raise Denied("claim capacity reached")
        # Replacing a device's code invalidates its prior display code.
        code = f"{secrets.randbelow(1000000):06d}"
        self.pending[address] = (code, secrets.token_hex(16), self.clock()+120)
        return {"code": code, "expires_in": 120}

    def verify(self, owner, address, proof):
        now = self.clock()
        self.attempts = {k:v for k,v in self.attempts.items() if v[1] > now}
        # A shared device budget prevents rotating user identities to guess a code.
        key = address
        if key not in self.attempts and len(self.attempts) >= 1024: raise Denied("claim attempt capacity reached")
        attempts, until = self.attempts.get(key, (0, now+120))
        if until <= now: attempts, until = 0, now+120
        if attempts >= 5: raise Denied("claim attempts exceeded")
        self.attempts[key] = (attempts+1, until)
        value = self.pending.get(address)
        if not isinstance(proof, str) or value is None or value[2] <= now or not secrets.compare_digest(proof, value[0]):
            raise Denied("invalid claim code")
        return value[1], value[2]
