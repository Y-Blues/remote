import hashlib
import hmac
import json
import time
import unittest

from ycappuccino.remote.models.remote_server import RemoteServer
from ycappuccino.remote.configured_peers import ConfiguredPeers
from ycappuccino.remote.peer_authentication import PeerHmacAuthentication
from ycappuccino.remote.stored_peers import StoredPeers

PEERS = {"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http", "secret": "s3cr3t"}}
PATH = "/api/services/__remote_dispatch__/pkg.IFoo/bar"


class FakeManager:
    def __init__(self, peers):
        self._peers = peers

    async def get_one(self, item_id, id, params=None, subject=None):
        assert item_id == "remoteServer"
        assert subject is None
        document = self._peers.get(id)
        if document is None:
            return None
        server = RemoteServer()
        server.id(id)
        server.host(document["host"])
        server.port(document["port"])
        server.scheme(document["scheme"])
        if document.get("secret") is not None:
            server.secret(document["secret"])
        return server


def _sign(secret, method, path, timestamp, subject_header, body):
    message = f"{method}\n{path}\n{timestamp}\n{subject_header}\n".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _headers(peer_id, secret, method="POST", path=PATH, body=b"", timestamp=None, subject=None):
    timestamp = timestamp if timestamp is not None else str(int(time.time()))
    subject_header = json.dumps(subject) if subject is not None else ""
    headers = {
        "x-ycappuccino-peer": peer_id,
        "x-ycappuccino-timestamp": timestamp,
        "x-ycappuccino-signature": _sign(secret, method, path, timestamp, subject_header, body),
    }
    if subject is not None:
        headers["x-ycappuccino-subject"] = subject_header
    return headers


class TestPeerHmacAuthentication(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.auth = PeerHmacAuthentication([StoredPeers(FakeManager(PEERS))])

    async def test_valid_signature_authenticates_as_the_peer(self):
        subject = await self.auth.authenticate(_headers("peer-a", "s3cr3t"), "POST", PATH, b"")

        self.assertEqual(subject, {"peer": "peer-a"})

    async def test_signature_covers_the_body(self):
        headers = _headers("peer-a", "s3cr3t", body=b'{"kwargs": {}}')

        self.assertEqual(await self.auth.authenticate(headers, "POST", PATH, b'{"kwargs": {}}'), {"peer": "peer-a"})
        self.assertIsNone(await self.auth.authenticate(headers, "POST", PATH, b'{"kwargs": {"x": 1}}'))

    async def test_forwarded_user_subject_is_returned_with_the_peer(self):
        headers = _headers("peer-a", "s3cr3t", subject={"sub": "alice", "tid": "acme"})

        subject = await self.auth.authenticate(headers, "POST", PATH, b"")

        self.assertEqual(subject, {"sub": "alice", "tid": "acme", "peer": "peer-a"})

    async def test_forwarded_subject_cannot_be_swapped_after_signing(self):
        headers = _headers("peer-a", "s3cr3t", subject={"sub": "alice", "tid": "acme"})
        headers["x-ycappuccino-subject"] = json.dumps({"sub": "superadmin", "tid": "system"})

        self.assertIsNone(await self.auth.authenticate(headers, "POST", PATH, b""))

    async def test_forwarded_subject_cannot_claim_another_peer(self):
        headers = _headers("peer-a", "s3cr3t", subject={"sub": "alice", "peer": "peer-b"})

        subject = await self.auth.authenticate(headers, "POST", PATH, b"")

        self.assertEqual(subject["peer"], "peer-a")

    async def test_missing_headers_is_not_recognized(self):
        self.assertIsNone(await self.auth.authenticate({}, "POST", PATH, b""))

    async def test_unknown_peer_is_not_recognized(self):
        self.assertIsNone(await self.auth.authenticate(_headers("unknown", "irrelevant"), "POST", PATH, b""))

    async def test_peer_without_secret_is_not_recognized(self):
        auth = PeerHmacAuthentication([StoredPeers(FakeManager({"peer-a": {**PEERS["peer-a"], "secret": None}}))])

        self.assertIsNone(await auth.authenticate(_headers("peer-a", ""), "POST", PATH, b""))

    async def test_wrong_secret_is_not_recognized(self):
        self.assertIsNone(await self.auth.authenticate(_headers("peer-a", "wrong"), "POST", PATH, b""))

    async def test_signature_bound_to_method_and_path(self):
        headers = _headers("peer-a", "s3cr3t")

        self.assertIsNone(await self.auth.authenticate(headers, "GET", PATH, b""))
        self.assertIsNone(await self.auth.authenticate(headers, "POST", "/api/services/other", b""))

    async def test_stale_timestamp_is_not_recognized(self):
        auth = PeerHmacAuthentication([StoredPeers(FakeManager(PEERS))], now=lambda: 1_000_000.0, tolerance=60.0)

        headers = _headers("peer-a", "s3cr3t", timestamp=str(1_000_000 - 61))

        self.assertIsNone(await auth.authenticate(headers, "POST", PATH, b""))

    async def test_timestamp_within_tolerance_is_recognized(self):
        auth = PeerHmacAuthentication([StoredPeers(FakeManager(PEERS))], now=lambda: 1_000_000.0, tolerance=60.0)

        headers = _headers("peer-a", "s3cr3t", timestamp=str(1_000_000 - 30))

        self.assertEqual(await auth.authenticate(headers, "POST", PATH, b""), {"peer": "peer-a"})

    async def test_malformed_timestamp_or_subject_is_not_recognized(self):
        headers = _headers("peer-a", "s3cr3t")
        headers["x-ycappuccino-timestamp"] = "not-a-number"
        self.assertIsNone(await self.auth.authenticate(headers, "POST", PATH, b""))

        headers = _headers("peer-a", "s3cr3t", subject={"sub": "alice"})
        headers["x-ycappuccino-subject"] = "{not json"
        self.assertIsNone(await self.auth.authenticate(headers, "POST", PATH, b""))


    async def test_a_peer_declared_in_the_configuration_is_recognized(self):
        auth = PeerHmacAuthentication([StoredPeers(FakeManager({})), ConfiguredPeers(peers="frontend", secret="shared")])

        subject = await auth.authenticate(_headers("frontend", "shared", subject={"sub": "alice"}), "POST", PATH, b"")

        self.assertEqual(subject, {"sub": "alice", "peer": "frontend"})


if __name__ == "__main__":
    unittest.main()
