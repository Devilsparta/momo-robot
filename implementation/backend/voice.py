"""Transport-independent Xiaozhi v1 session adapter. No fabricated ASR/TTS.
Provider injection is explicit. No default external provider, memory, or tools.
"""
from dataclasses import dataclass
from .device_store import Denied


@dataclass(frozen=True)
class Privacy:
    audio_egress: bool = False
    transcript_egress: bool = False
    image_egress: bool = False
    memory_egress: bool = False

    def __post_init__(self):
        if any(type(v) is not bool for v in vars(self).values()):
            raise ValueError("privacy fields must be boolean")


class ProtocolError(ValueError):
    pass


class VoiceSession:
    def __init__(self, session_id, authorize, privacy=None, provider=None, max_bytes=32000):
        if not isinstance(session_id, str) or not session_id or max_bytes <= 0:
            raise ValueError("invalid session")
        self.id = session_id
        self.authorize = authorize  # trusted store.check_session closure
        self.privacy = privacy or Privacy()
        self.provider = provider
        self.max_bytes = max_bytes
        self.state = "new"
        self.audio = []
        self.bytes = 0

    def clear(self):
        self.audio.clear()
        self.bytes = 0

    def check_authorized(self):
        try:
            self.authorize()
        except Exception:
            self.close()
            raise

    def receive(self, message):
        self.check_authorized()
        if self.state == "closed":
            raise ProtocolError("closed")
        if isinstance(message, bytes):
            if self.state != "listening" or not message:
                raise ProtocolError("unexpected audio")
            if self.bytes + len(message) > self.max_bytes:
                self.clear()
                self.state = "ready"
                raise ProtocolError("audio budget exceeded")
            self.audio.append(message)
            self.bytes += len(message)
            return []
        if not isinstance(message, dict):
            raise ProtocolError("JSON object required")
        if message.get("type") == "hello":
            if self.state != "new":
                raise ProtocolError("duplicate hello")
            params = message.get("audio_params")
            if (type(message.get("version")) is not int or message["version"] != 1
                    or message.get("transport") != "websocket"
                    or not isinstance(params, dict)
                    or any(type(params.get(k)) is not int for k in ("sample_rate", "channels", "frame_duration"))
                    or params != {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}):
                raise ProtocolError("only negotiated v1 opus/16000/mono/60ms supported")
            self.state = "ready"
            return [{"type": "hello", "version": 1, "transport": "websocket",
                     "session_id": self.id, "audio_params": dict(params)}]
        if self.state == "new" or message.get("session_id") != self.id:
            raise ProtocolError("hello or matching session required")
        kind = message.get("type")
        if kind == "abort":
            self.clear()
            self.state = "ready"
            return []
        if kind == "mcp":
            raise Denied("MCP execution is disabled pending tool authorization")
        if kind != "listen":
            raise ProtocolError("unsupported message")
        state = message.get("state")
        if state == "start":
            if self.state != "ready" or message.get("mode") != "manual":
                raise ProtocolError("only manual turn mode currently implemented")
            self.clear()
            self.state = "listening"
            return []
        if state != "stop" or self.state != "listening":
            raise ProtocolError("invalid listen transition")
        frames = tuple(self.audio)
        self.clear()
        self.state = "ready"
        if not frames:
            return []
        # No default provider; an explicitly approved adapter must declare data categories.
        if self.provider is None:
            raise Denied("voice provider not configured")
        categories = self.provider.egress_categories
        if not isinstance(categories, frozenset) or not categories <= {"audio", "transcript", "image", "memory"}:
            raise Denied("provider data declaration invalid")
        if any(not getattr(self.privacy, c + "_egress") for c in categories):
            raise Denied("provider egress not authorized")
        self.check_authorized()
        # Adapter contract returns iterable of valid Opus frames at negotiated 16k mono/60ms.
        # Codec validity must be checked by an actual provider/codec integration later.
        packets = []
        total = 0
        for frame in self.provider.respond(frames):
            self.check_authorized()
            if not isinstance(frame, bytes) or not frame:
                raise ProtocolError("provider returned invalid frame")
            total += len(frame)
            if total > self.max_bytes:
                raise ProtocolError("provider output budget exceeded")
            packets.append(frame)
        self.check_authorized()
        return [{"type": "tts", "state": "start", "session_id": self.id},
                *packets, {"type": "tts", "state": "stop", "session_id": self.id}]

    def close(self):
        self.clear()
        self.state = "closed"
