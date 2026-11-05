"""Existing remote control frame codec; no socket or motor side effects."""
import struct
from .device_store import Denied

KNOWN = set(range(1, 8)) | set(range(9, 16)) | set(range(16, 28))
# Remote Go server mapping, NOT the LAN decoder's prefix rules.
TARGETED = {1, 2, 3, 4, 7, 9}
ADDRESS_ONLY = {5, 6, 0x12, 0x13, 0x18, 0x19}


def decode(frame, max_payload=256*1024-5):
    if not isinstance(frame, bytes) or len(frame) < 5:
        raise ValueError("truncated frame")
    kind, size = struct.unpack(">BI", frame[:5])
    if kind not in KNOWN or size > max_payload or size != len(frame) - 5:
        raise ValueError("invalid frame")
    return kind, frame[5:]


def encode(kind, payload=b""):
    if kind not in KNOWN or not isinstance(payload, bytes) or len(payload) > 256*1024-5:
        raise ValueError("invalid message")
    return struct.pack(">BI", kind, len(payload)) + payload


def route(frame, session, store, permission):
    store.check_session(session)
    kind, payload = decode(frame)
    # Explicit permission hook applies also to camera/audio/unknown message routing.
    if permission(kind) is not True:
        raise Denied("control not authorized")
    if kind in ADDRESS_ONLY:
        if payload != session[1].encode("ascii"):
            raise Denied("wrong target")
        # Official remote server preserves the address payload for these messages.
    if kind in TARGETED:
        if len(payload) < 12 or payload[:12].decode("ascii", errors="replace").lower() != session[1]:
            raise Denied("wrong target")
        payload = payload[12:]
    return encode(kind, payload)
