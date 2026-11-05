import hashlib
import hmac
import json
import sqlite3
import unittest
from backend.ota import validate_release, verify_artifact
from backend.device_store import DeviceStore, Denied
from backend.access import Access
from backend.identity import Identity


class Release(unittest.TestCase):
    def setUp(self):
        self.data=b"synthetic-firmware-not-flashable"
        self.manifest=dict(model="K151",chip="esp32s3",version="0.0.2",sequence=2,
                           size=len(self.data),sha256=hashlib.sha256(self.data).hexdigest(),
                           url="https://updates.example.invalid/test.bin",expires=200)
        # Synthetic verifier for adapter contract testing, not production signing.
        self.key=b"public-test-fixture"

    def sign(self,m):
        return hmac.new(self.key,json.dumps(m,sort_keys=True,separators=(",",":")).encode(),"sha256").hexdigest()

    def validate(self,m,signature=None,verify=True):
        verifier=(lambda data,sig:hmac.compare_digest(hmac.new(self.key,data,"sha256").hexdigest(),sig)) if verify else None
        return validate_release(m,signature or self.sign(m),verifier,current_sequence=1,capacity=100,
                                allowed_hosts={"updates.example.invalid"},now=100)

    def test_release_and_stream_size_hash(self):
        manifest=self.validate(self.manifest)
        self.assertTrue(verify_artifact(manifest,[self.data[:3],self.data[3:]]))
        for chunks in ([self.data+b"x"],[self.data[:-1]],[b"x"*len(self.data)]):
            with self.assertRaises(ValueError):verify_artifact(manifest,chunks)

    def test_target_rollback_origin_signature_capacity(self):
        changes={"model":"other","chip":"esp32","sequence":1,"size":101,"sha256":"G"*64,"expires":99,
                 "version":"bad\nversion"}
        for field,value in changes.items():
            with self.subTest(field=field),self.assertRaises(ValueError):self.validate(dict(self.manifest,**{field:value}))
        for url in ("http://updates.example.invalid/a","https://other.example/a","https://user@updates.example.invalid/a",
                    "https://updates.example.invalid:444/a","https://updates.example.invalid/a#fragment"):
            with self.subTest(url=url),self.assertRaises(ValueError):self.validate(dict(self.manifest,url=url))
        with self.assertRaises(ValueError):self.validate(self.manifest,verify=False)
        with self.assertRaises(ValueError):self.validate(dict(self.manifest,version="0.0.3"),self.sign(self.manifest))


class IdentityLimits(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.addCleanup(self.db.close)
        self.store=DeviceStore(self.db);self.access=Access(self.store)
        self.clock=100
        self.identity=Identity(self.store,self.access,lambda:self.clock)
        self.identity.register("020000000001","test",lambda m,p:p=="synthetic-proof")

    def test_invalid_proof_consumed_and_rate_limited(self):
        c=self.identity.challenge("020000000001")
        with self.assertRaises(Denied):self.identity.prove(c["challenge_id"],"wrong")
        with self.assertRaises(Denied):self.identity.prove(c["challenge_id"],"synthetic-proof")
        for _ in range(4):self.identity.challenge("020000000001")
        with self.assertRaises(Denied):self.identity.challenge("020000000001")
        self.clock+=61
        self.identity.challenge("020000000001")

    def test_expiry_rotation_and_revocation(self):
        c=self.identity.challenge("020000000001");self.clock+=61
        with self.assertRaises(Denied):self.identity.prove(c["challenge_id"],"synthetic-proof")
        c=self.identity.challenge("020000000001")
        self.identity.register("020000000001","replacement",lambda m,p:True)
        with self.assertRaises(Denied):self.identity.prove(c["challenge_id"],"synthetic-proof")
        c=self.identity.challenge("020000000001")
        token=self.identity.prove(c["challenge_id"],"synthetic-proof")["device_token"]
        self.identity.revoke("020000000001")
        with self.assertRaises(Denied):self.access.authenticate({"authorization":"Bearer "+token})
