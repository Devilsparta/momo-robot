"""Authenticated upstream OTA check, plus offline release validation.

No release URL is advertised and no artifact is downloaded or flashed. A verifier
is injected for review/testing; this does not establish a production trust root.
"""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit


def no_update(data):
    app = data.get("application")
    version = app.get("version") if isinstance(app,dict) else None
    if not isinstance(version,str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}",version):
        raise ValueError("application version required")
    # Ota::CheckVersion consumes a raw object, not a GoFrame response envelope.
    return {"firmware":{"version":version}, "websocket":{"version":1},
            "server_time":{"timestamp":int(time.time()*1000), "timezone_offset":0}}


def validate_release(manifest, signature, verify, *, current_sequence, capacity, allowed_hosts, now=None):
    """Fail closed on identity, rollback, origin, signature, or size mismatches.

    Sequence is a signed release counter, independent of human version strings.
    The caller must persist an accepted counter only after a successful deployment.
    """
    keys={"model","chip","version","sequence","size","sha256","url","expires"}
    if not isinstance(manifest,dict) or set(manifest)!=keys: raise ValueError("release fields")
    m=manifest
    if m["model"]!="K151" or m["chip"]!="esp32s3": raise ValueError("release target")
    if not isinstance(m["version"],str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}",m["version"]): raise ValueError("release version")
    if type(m["sequence"]) is not int or m["sequence"]<=current_sequence: raise ValueError("rollback")
    if type(m["size"]) is not int or not 0<m["size"]<=capacity: raise ValueError("release capacity")
    if not isinstance(m["sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",m["sha256"]): raise ValueError("release digest")
    now=time.time() if now is None else now
    if type(m["expires"]) is not int or m["expires"]<=now: raise ValueError("release expired")
    if not isinstance(m["url"],str) or any(ord(c)<=32 or ord(c)>=127 for c in m["url"]): raise ValueError("release URL")
    u=urlsplit(m["url"])
    if u.scheme!="https" or u.hostname not in allowed_hosts or u.username or u.password or u.fragment or u.port not in (None,443) or not u.path.startswith("/"):
        raise ValueError("release origin")
    canonical=json.dumps(m,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")
    if verify is None or not isinstance(signature,str) or not 1<=len(signature)<=4096 or verify(canonical,signature) is not True:
        raise ValueError("release signature")
    return dict(m)


def verify_artifact(manifest, chunks):
    """Bounded streaming verification only; never writes a device or file."""
    digest=hashlib.sha256();size=0
    for chunk in chunks:
        if not isinstance(chunk,bytes): raise ValueError("artifact bytes required")
        size+=len(chunk)
        if size>manifest["size"]: raise ValueError("artifact too large")
        digest.update(chunk)
    if size!=manifest["size"] or digest.hexdigest()!=manifest["sha256"]: raise ValueError("artifact mismatch")
    return True


class ReleaseCatalog:
    """One bounded, verified in-memory release; trusted staging API, no HTTP upload.

    Deployment can replace storage later. Devices only see a release explicitly
    assigned to them, and the HTTP service rechecks binding before sending bytes.
    """
    def __init__(self, clock=time.time):
        self.clock=clock
        self.release=None
        self.artifact=None
        self.devices=frozenset()

    def stage(self, manifest, signature, artifact, devices, verify, *, current_sequence, allowed_hosts):
        import re
        if type(artifact) is not bytes or len(artifact)>5177344:
            raise ValueError("bounded firmware bytes required")
        targets=frozenset(devices)
        if not 1<=len(targets)<=128 or any(not isinstance(x,str) or not re.fullmatch(r"[0-9a-f]{12}",x) for x in targets):
            raise ValueError("explicit device targets required")
        m=validate_release(manifest,signature,verify,current_sequence=current_sequence,
            capacity=5177344,allowed_hosts=allowed_hosts,now=self.clock())
        url=urlsplit(m["url"])
        if url.path!="/private/v1/releases/"+m["sha256"] or url.query:
            raise ValueError("immutable private release path required")
        verify_artifact(m,[artifact])
        self.release={"manifest":m,"signature":signature}
        self.artifact=artifact
        self.devices=targets

    def withdraw(self):
        self.release=self.artifact=None
        self.devices=frozenset()

    def offer(self, device):
        if not self.release or device not in self.devices or self.release["manifest"]["expires"]<=self.clock():return None
        return {"manifest":dict(self.release["manifest"]),"signature":self.release["signature"]}

    def download(self, device, digest):
        release=self.offer(device)
        if not release or digest!=release["manifest"]["sha256"]:return None
        return self.artifact
