"""Synthetic release bytes only: no flashable image, actual signer or device."""
import asyncio
import hashlib
import hmac
import json
import unittest
from backend.ota import ReleaseCatalog
import test_service as harness
from test_service import MAC,OTHER

DATA=b"synthetic-not-firmware"*1000
DIGEST=hashlib.sha256(DATA).hexdigest()
MANIFEST=dict(model="K151",chip="esp32s3",version="0.0.2",sequence=2,size=len(DATA),
    sha256=DIGEST,url="https://updates.example.invalid/private/v1/releases/"+DIGEST,expires=200)
def sign(m):
    return hmac.new(b"public-test-fixture",json.dumps(m,sort_keys=True,separators=(",",":")).encode(),"sha256").hexdigest()
def verify(data,sig):
    return hmac.compare_digest(hmac.new(b"public-test-fixture",data,"sha256").hexdigest(),sig)
def stage(catalog,manifest=None,data=DATA):
    manifest=manifest or MANIFEST
    catalog.stage(manifest,sign(manifest),data,{MAC},verify,current_sequence=1,allowed_hosts={"updates.example.invalid"})

class Catalog(unittest.TestCase):
    def test_validated_immutable_targeted_release_expiry_and_withdrawal(self):
        now=100;c=ReleaseCatalog(lambda:now);stage(c)
        offer=c.offer(MAC);offer["manifest"]["size"]=1
        self.assertEqual(c.offer(MAC)["manifest"],MANIFEST)
        self.assertIsNone(c.offer(OTHER));self.assertIsNone(c.download(MAC,"0"*64))
        self.assertEqual(c.download(MAC,DIGEST),DATA)
        now=201;self.assertIsNone(c.offer(MAC));self.assertIsNone(c.download(MAC,DIGEST))
        now=100;c.withdraw();self.assertIsNone(c.offer(MAC))

    def test_no_unsigned_tampered_or_ambiguous_release(self):
        c=ReleaseCatalog(lambda:100)
        with self.assertRaises(ValueError):stage(c,data=DATA[:-1])
        with self.assertRaises(ValueError):stage(c,dict(MANIFEST,url="https://updates.example.invalid/anything"))
        with self.assertRaises(ValueError):c.stage(MANIFEST,"bad",DATA,{MAC},verify,current_sequence=1,allowed_hosts={"updates.example.invalid"})
        self.assertIsNone(c.offer(MAC))

class Delivery(unittest.IsolatedAsyncioTestCase):
    http=harness.Integration.http;bind=harness.Integration.bind
    asyncSetUp=harness.Integration.asyncSetUp;asyncTearDown=harness.Integration.asyncTearDown

    async def download(self,token):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.s.port)
        writer.write(f"GET /private/v1/releases/{DIGEST} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\nContent-Length: 0\r\n\r\n".encode())
        await writer.drain();result=await asyncio.wait_for(reader.read(),2)
        writer.close();await writer.wait_closed()
        return result.split(b"\r\n\r\n",1)

    async def test_authenticated_raw_offer_and_download_binding(self):
        self.s.releases=ReleaseCatalog(lambda:100);stage(self.s.releases)
        token=await self.bind()
        status,result=await self.http("/xiaozhi/ota/","POST",{"application":{"version":"0.0.1"}},token,"Device-Id: "+MAC+"\r\n")
        self.assertEqual(status,200);self.assertEqual(result["private_release"]["manifest"],MANIFEST)
        self.assertNotIn("url",result["firmware"])
        head,body=await self.download(token)
        self.assertIn(b"200 OK",head);self.assertEqual(body,DATA)
        self.assertIn(b"403",(await self.download(self.bt))[0])
        await self.http("/stackChan/v2/device/unbind","POST",{"mac":MAC},self.at)
        self.assertIn(b"403",(await self.download(token))[0])

    async def test_withdrawal_truncates_body_without_a_second_response(self):
        class Withdraw(ReleaseCatalog):
            count=0
            def download(self,*args):
                self.count+=1
                if self.count==3:self.withdraw()
                return super().download(*args)
        self.s.releases=Withdraw(lambda:100);stage(self.s.releases)
        token=await self.bind();head,body=await self.download(token)
        self.assertIn(b"200 OK",head)
        self.assertGreater(len(body),0);self.assertLess(len(body),len(DATA))
        self.assertNotIn(b"HTTP/1.1",body)
