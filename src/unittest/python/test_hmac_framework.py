"""
Peer HMAC authentication across two real processes (spec 2026-09-16-transparent-rpc-design.md,
sections 4 and 11.2). The peer runs in a subprocess, loads only the remote modules it needs
(peer_authentication + models, listed individually in bundle_prefix, section 11.6) next to
endpoints_service/http_server, and exposes a secure service answering with the subject it received.
It registers this test process as the RemoteServer "caller" with the shared secret. The caller (this
process) calls it through RemoteCall.
"""

import asyncio
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

from ycappuccino.api.endpoints_storage import NotAuthenticated
from ycappuccino.core.framework import Framework
from ycappuccino.core.testing import TemporaryApplication, wait_until

PEER_PORT = 18163
SECRET = "s3cr3t-shared-by-both-sides"

PEER_APPLICATION = {
    "conf/application.yml": f"""
        name: hmacpeer
        bundle_prefix:
          - ycappuccino.storage
          - ycappuccino.endpoints_storage
          - ycappuccino.endpoints_service
          - ycappuccino.http_server
          - ycappuccino.remote.models
          - ycappuccino.remote.peer_authentication
          - ycappuccino.remote.stored_peers
          - ycappuccino.remote.capabilities
          - PACKAGE
        layers:
          ycappuccino_storage_memory:
            active: true
        config:
          http_server:
            active: true
            port: {PEER_PORT}
            ip: localhost
          shell:
            console: false
    """,
    "PACKAGE/__init__.py": "",
    "PACKAGE/peer.py": f"""
        from ycappuccino.api.endpoints_service import IExposedService, ServiceResult
        from ycappuccino.api.endpoints_storage import IAuthorization
        from ycappuccino.api.storage import IManager
        from ycappuccino.remote.models.remote_server import RemoteServer


        class WhoAmI(IExposedService):
            name = "whoami"
            secure = True

            def __init__(self):
                pass

            async def call(self, method, extra_path, params, body, subject):
                return ServiceResult(body={{"subject": subject}})

            async def start(self):
                pass

            async def stop(self):
                pass


        class PeersOnly(IAuthorization):
            def __init__(self):
                pass

            async def is_authorized(self, subject, action, item_id):
                return "peer" in subject

            async def start(self):
                pass

            async def stop(self):
                pass


        class RegisterCaller(IExposedService):
            name = "register_caller"
            secure = False

            def __init__(self, manager: IManager):
                self._manager = manager

            async def call(self, method, extra_path, params, body, subject):
                server = RemoteServer()
                server.id("hmaccaller")
                server.host("localhost")
                server.port(1)
                server.scheme("http")
                server.secret("{SECRET}")
                await self._manager.up_sert_model(server, subject=None)
                return ServiceResult(body={{}})

            async def start(self):
                pass

            async def stop(self):
                pass
    """,
}

CALLER_APPLICATION = {
    "conf/application.yml": """
        name: hmaccaller
        bundle_prefix:
          - ycappuccino.storage
          - ycappuccino.endpoints_service
          - ycappuccino.remote
        layers:
          ycappuccino_storage_memory:
            active: true
        config:
          shell:
            console: false
    """,
}


def _peer_is_up(port):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/items", timeout=1):
            return True
    except (urllib.error.URLError, ConnectionError):
        return False


def _post(port, service):
    request = urllib.request.Request(f"http://localhost:{port}/api/services/{service}", data=b"{}", method="POST")
    with urllib.request.urlopen(request, timeout=5):
        pass


class TestPeerHmacAcrossTwoProcesses(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.peer_app = TemporaryApplication(PEER_APPLICATION).open()
        cls.addClassCleanup(cls.peer_app.close)
        cls.peer_process = subprocess.Popen(
            [sys.executable, "-m", "ycappuccino.core.runner", "--root_path", cls.peer_app.root],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.addClassCleanup(cls.peer_process.wait, 5)
        cls.addClassCleanup(cls.peer_process.terminate)
        if not wait_until(lambda: _peer_is_up(PEER_PORT), timeout=10):
            raise RuntimeError("peer subprocess did not expose its HTTP port in time")
        _post(PEER_PORT, "register_caller")

        cls.caller_app = TemporaryApplication(CALLER_APPLICATION).open()
        cls.addClassCleanup(cls.caller_app.close)
        cls.framework = Framework()
        cls.framework.init(cls.caller_app.yml_path)
        cls.addClassCleanup(cls.framework.stop)
        wait_until(lambda: cls.framework.context.get_service_reference("RemoteCall"))

        context = cls.framework.context
        manager = context.get_service(context.get_service_reference("IManager"))
        from ycappuccino.remote.models.remote_server import RemoteServer

        for peer_id, secret in (("peer", SECRET), ("peer-with-wrong-secret", "not-the-shared-secret")):
            server = RemoteServer()
            server.id(peer_id)
            server.host("localhost")
            server.port(PEER_PORT)
            server.scheme("http")
            server.secret(secret)
            asyncio.run(manager.up_sert_model(server, subject=None))
        cls.remote_call = context.get_service(context.get_service_reference("RemoteCall"))
        cls.catalog = context.get_service(context.get_service_reference("ServiceCatalog"))

    def test_a_signed_call_authenticates_as_the_peer(self):
        result = asyncio.run(self.remote_call.call("POST", ["peer", "whoami"], {}, {}, None))

        self.assertEqual(result.body, {"subject": {"peer": "hmaccaller"}})

    def test_the_user_the_call_is_made_for_reaches_the_peer(self):
        result = asyncio.run(
            self.remote_call.call("POST", ["peer", "whoami"], {}, {}, {"sub": "alice", "tid": "acme"})
        )

        self.assertEqual(result.body, {"subject": {"sub": "alice", "tid": "acme", "peer": "hmaccaller"}})

    def test_the_catalog_stores_the_full_signatures_a_signed_peer_describes(self):
        from ycappuccino.api.storage import IManager
        from ycappuccino.remote.signatures import describe_interface

        manager_path = "ycappuccino.api.storage.IManager"

        specifications = asyncio.run(self.catalog.refresh_peer("peer"))
        located = asyncio.run(self.catalog.locate(manager_path))

        self.assertIn(manager_path, specifications)
        peer_entry = [entry for entry in located if entry["peer_id"] == "peer"]
        self.assertEqual(
            peer_entry,
            [{"peer_id": "peer", "host": "localhost", "port": PEER_PORT, "scheme": "http",
              "methods": describe_interface(IManager, public_only=False)}],
        )

    def test_the_catalog_of_an_unauthenticated_peer_holds_no_internal_interface(self):
        # a rejected signature leaves an anonymous caller: capabilities answers its public view only
        specifications = asyncio.run(self.catalog.refresh_peer("peer-with-wrong-secret"))

        self.assertNotIn("ycappuccino.api.storage.IManager", specifications)
        located = asyncio.run(self.catalog.locate("ycappuccino.api.storage.IManager"))
        self.assertNotIn("peer-with-wrong-secret", [entry["peer_id"] for entry in located])

    def test_a_wrong_secret_is_not_authenticated(self):
        with self.assertRaises(NotAuthenticated):
            asyncio.run(self.remote_call.call("POST", ["peer-with-wrong-secret", "whoami"], {}, {}, None))


if __name__ == "__main__":
    unittest.main()
