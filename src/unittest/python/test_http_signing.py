import json
import unittest

from test_remote_call import FakeOpener

from ycappuccino.remote._http import call_peer
from ycappuccino.remote.peer_authentication import sign

PEER = {"_id": "peer-a", "host": "peer.example", "port": 9000, "scheme": "http"}


def _header(request, name):
    return {key.lower(): value for key, value in request.header_items()}.get(name.lower())


class TestCallPeerSigning(unittest.TestCase):

    def test_a_peer_with_a_secret_gets_a_verifiable_signature(self):
        opener = FakeOpener()

        call_peer({**PEER, "secret": "s3cr3t"}, "echo", "POST", [], {"limit": "5"}, {"msg": "hi"},
                  opener=opener, local_peer_id="node-1")

        request = opener.requests[0]
        self.assertEqual(_header(request, "X-YCappuccino-Peer"), "node-1")
        timestamp = _header(request, "X-YCappuccino-Timestamp")
        expected = sign("s3cr3t", "POST", "/api/services/echo", timestamp, "", request.data)
        self.assertEqual(_header(request, "X-YCappuccino-Signature"), expected)
        self.assertIsNone(_header(request, "X-YCappuccino-Subject"))

    def test_the_subject_is_forwarded_and_signed(self):
        opener = FakeOpener()

        call_peer({**PEER, "secret": "s3cr3t"}, "echo", "POST", [], None, None,
                  opener=opener, local_peer_id="node-1", subject={"sub": "alice", "tid": "acme"})

        request = opener.requests[0]
        subject_header = _header(request, "X-YCappuccino-Subject")
        self.assertEqual(json.loads(subject_header), {"sub": "alice", "tid": "acme"})
        timestamp = _header(request, "X-YCappuccino-Timestamp")
        expected = sign("s3cr3t", "POST", "/api/services/echo", timestamp, subject_header, b"")
        self.assertEqual(_header(request, "X-YCappuccino-Signature"), expected)

    def test_a_peer_without_a_secret_gets_no_signature_nor_subject(self):
        opener = FakeOpener()

        call_peer(PEER, "echo", "POST", [], None, None, opener=opener, local_peer_id="node-1",
                  subject={"sub": "alice"})

        request = opener.requests[0]
        for name in ("X-YCappuccino-Peer", "X-YCappuccino-Timestamp", "X-YCappuccino-Signature", "X-YCappuccino-Subject"):
            self.assertIsNone(_header(request, name))



class NotJsonOpener:
    """a peer still starting: its HTTP server answers before its API exists, with a non-JSON page"""

    def __init__(self, status):
        self.status = status

    def __call__(self, request, timeout=None):
        import io
        import urllib.error

        raise urllib.error.HTTPError(request.full_url, self.status, "Not Found", {}, io.BytesIO(b"<html>404</html>"))


class TestCallPeerErrors(unittest.TestCase):

    def test_a_non_json_error_keeps_its_status(self):
        from ycappuccino.api.endpoints_storage import NotFound

        with self.assertRaises(NotFound) as raised:
            call_peer(PEER, "echo", "GET", [], None, None, opener=NotJsonOpener(404))
        self.assertIn("404", str(raised.exception))
        with self.assertRaises(RuntimeError):
            call_peer(PEER, "echo", "GET", [], None, None, opener=NotJsonOpener(503))


if __name__ == "__main__":
    unittest.main()
