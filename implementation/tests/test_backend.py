import copy
import sqlite3
import unittest
from backend.device_store import DeviceStore, Principal, Denied, mac
from backend.voice import VoiceSession, Privacy, ProtocolError
from backend.control import encode, decode, route

A = Principal("alice", "user")
B = Principal("bob", "user")
MAC = "020000000001"
HELLO = {"type": "hello", "version": 1, "transport": "websocket",
         "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}}

def verifier(owner, address, proof):
    if proof != (owner, address, "synthetic-possession"):
        raise Denied("invalid proof")
    return owner + address, 100

class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.store = DeviceStore(self.db, verifier, lambda: 1)

    def bind(self, owner=A):
        return self.store.bind(owner, MAC, (owner.subject, MAC, "synthetic-possession"))

    def test_no_verifier_fails_closed(self):
        self.store.verify_claim = None
        with self.assertRaises(Denied): self.bind()

    def test_mac_canonicalization(self):
        self.assertEqual(mac("02:00:00:00:00:01"), MAC)
        for value in ("", "02::::0000000001", "zz0000000001"):
            with self.assertRaises(ValueError): mac(value)

    def test_binding_needs_proof_for_this_user(self):
        with self.assertRaises(Denied):
            self.store.bind(B, MAC, ("alice", MAC, "synthetic-possession"))
        self.assertEqual(self.store.list_devices(B), [])

    def test_replay_and_cross_owner_rejected(self):
        self.bind()
        with self.assertRaises(Denied): self.bind()
        with self.assertRaises(Denied): self.bind(B)
        self.assertEqual(len(self.store.list_devices(A)), 1)
        self.assertEqual(self.store.list_devices(B), [])

    def test_expired_proof_rejected(self):
        self.store.clock = lambda: 100
        with self.assertRaises(Denied): self.bind()

    def test_device_identity_not_a_user(self):
        with self.assertRaises(Denied):
            self.store.list_devices(Principal(MAC, "device"))

    def test_settings_are_owned_and_validated(self):
        self.bind()
        with self.assertRaises(Denied): self.store.rename(B, MAC, "stolen")
        for config in ({"volume": True}, {"brightness": 101}, {"provider_key": "x"}):
            with self.assertRaises(ValueError): self.store.configure(A, MAC, config)
        self.store.configure(A, MAC, {"volume": 20})
        self.assertEqual(self.store.get_config(A, MAC), {"volume": 20})
        self.store.rename(A, MAC, "小伙伴")
        self.assertEqual(self.store.list_devices(A)[0]["name"], "小伙伴")

    def test_unbind_revokes_session_and_clears_config(self):
        self.bind()
        session = self.store.session(A, MAC)
        self.store.configure(A, MAC, {"volume": 20})
        self.store.unbind(A, MAC)
        with self.assertRaises(Denied): self.store.check_session(session)
        self.bind(B)
        self.assertEqual(self.store.get_config(B, MAC), {})

    def test_control_target_and_permission(self):
        self.bind(); session = self.store.session(A, MAC)
        frame = encode(3, MAC.encode() + b"{}")
        self.assertEqual(decode(route(frame, session, self.store, lambda _: True)), (3, b"{}"))
        with self.assertRaises(Denied): route(frame, session, self.store, lambda _: False)
        with self.assertRaises(Denied): route(encode(3, b"020000000002{}"), session, self.store, lambda _: True)
        self.store.unbind(A, MAC)
        with self.assertRaises(Denied): route(frame, session, self.store, lambda _: True)

    def test_remote_target_mapping_differs_from_lan(self):
        self.bind(); session = self.store.session(A, MAC)
        for kind in (1,2,3,4,7,9):
            frame = encode(kind, MAC.encode()+b"payload")
            self.assertEqual(decode(route(frame,session,self.store,lambda _:True)),(kind,b"payload"))
        for kind in (5,6,0x12,0x13,0x18,0x19):
            frame = encode(kind,MAC.encode())
            self.assertEqual(route(frame,session,self.store,lambda _:True),frame)
            with self.assertRaises(Denied):route(encode(kind,b"020000000002"),session,self.store,lambda _:True)

class Provider:
    egress_categories = frozenset({"audio"})
    calls = 0
    def respond(self, frames):
        self.calls += 1
        return (b"synthetic-opus-placeholder",)

class VoiceTests(unittest.TestCase):
    def session(self, provider=None, privacy=None, authorize=lambda: None):
        v = VoiceSession("test-session", authorize, provider=provider, privacy=privacy)
        v.receive(copy.deepcopy(HELLO))
        return v

    def command(self, v, kind="listen", **kwargs):
        return v.receive({"type": kind, "session_id": "test-session", **kwargs})

    def record(self, v):
        self.command(v, state="start", mode="manual")
        v.receive(b"synthetic-input")

    def test_no_provider_does_not_fake_speech(self):
        v = self.session(); self.record(v)
        with self.assertRaises(Denied): self.command(v, state="stop")
        self.assertEqual(v.audio, [])

    def test_default_privacy_blocks_provider(self):
        p = Provider(); v = self.session(p); self.record(v)
        with self.assertRaises(Denied): self.command(v, state="stop")
        self.assertEqual(p.calls, 0)

    def test_opt_in_provider_envelopes(self):
        p = Provider(); v = self.session(p, Privacy(audio_egress=True))
        self.record(v); result = self.command(v, state="stop")
        self.assertEqual((result[0]["state"], result[-1]["state"]), ("start", "stop"))
        self.assertEqual(p.calls, 1)

    def test_extra_egress_category_needs_separate_consent(self):
        p = Provider(); p.egress_categories = frozenset({"audio", "memory"})
        v = self.session(p, Privacy(audio_egress=True)); self.record(v)
        with self.assertRaises(Denied): self.command(v, state="stop")
        self.assertEqual(p.calls, 0)

    def test_abort_discards_turn(self):
        p = Provider(); v = self.session(p); self.record(v)
        self.command(v, "abort")
        self.assertEqual(v.audio, [])
        with self.assertRaises(ProtocolError): self.command(v, state="stop")
        self.assertEqual(p.calls, 0)

    def test_protocol_rejects_wrong_session_version_and_mode(self):
        v = self.session()
        with self.assertRaises(ProtocolError): v.receive({"type": "abort", "session_id": "other"})
        with self.assertRaises(ProtocolError): self.command(v, state="start", mode="auto")
        for version in (2, 3, True):
            h = copy.deepcopy(HELLO); h["version"] = version
            with self.assertRaises(ProtocolError): VoiceSession("x", lambda: None).receive(h)

    def test_input_output_budgets(self):
        v = self.session(); v.max_bytes = 3
        self.command(v, state="start", mode="manual")
        with self.assertRaises(ProtocolError): v.receive(b"1234")
        self.assertEqual(v.audio, [])
        p = Provider(); v = self.session(p, Privacy(audio_egress=True)); v.max_bytes=20
        self.record(v)
        with self.assertRaises(ProtocolError): self.command(v, state="stop")

    def test_revocation_prevents_further_data(self):
        allowed = [True]
        def auth():
            if not allowed[0]: raise Denied("revoked")
        v = self.session(authorize=auth)
        self.record(v)
        allowed[0] = False
        with self.assertRaises(Denied): self.command(v, state="stop")
        self.assertEqual(v.audio, [])
        self.assertEqual(v.state, "closed")

    def test_tools_and_malformed_policy_denied(self):
        with self.assertRaises(ValueError): Privacy(audio_egress="false")
        with self.assertRaises(Denied): self.command(self.session(), "mcp", payload={})

class CodecTests(unittest.TestCase):
    def test_literal_ping(self):
        self.assertEqual(encode(0x10), bytes.fromhex("1000000000"))

    def test_invalid_frames(self):
        for data in (b"", b"\x10", bytes.fromhex("1000000001"), bytes.fromhex("ff00000000")):
            with self.assertRaises(ValueError): decode(data)
        with self.assertRaises(ValueError): encode(0x08)
