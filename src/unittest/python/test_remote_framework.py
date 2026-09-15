"""
The one real end-to-end test: a peer instance runs in a genuine subprocess (a second OS
process, not a second in-process Framework -- only one Pelix framework can exist per
process, see core/README.md), exposing a real IExposedService over http_server. The
calling instance runs in this test process, with a RemoteServer registered towards that
peer, and calls RemoteCall through a real IServiceEndpoint.
"""

import asyncio
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

from ycappuccino.core.framework import Framework
from ycappuccino.core.testing import TemporaryApplication, wait_until

PEER_PORT = 18160

PEER_APPLICATION = {
    "conf/application.yml": f"""
        name: remotepeertest
        bundle_prefix:
          - ycappuccino.storage
          - ycappuccino.endpoints_storage
          - ycappuccino.endpoints_service
          - ycappuccino.http_server
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
    "PACKAGE/greeting.py": """
        from ycappuccino.api.endpoints_service import IExposedService, ServiceResult


        class Greeting(IExposedService):
            name = "greeting"
            secure = False

            def __init__(self):
                pass

            async def call(self, method, extra_path, params, body, subject):
                return ServiceResult(body={"greeting": f"hello {body['name']}"})

            async def start(self):
                pass

            async def stop(self):
                pass
    """,
}

CALLER_APPLICATION = {
    "conf/application.yml": """
        name: remotecallertest
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


class TestRemoteAcrossTwoProcesses(unittest.TestCase):

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
        found = wait_until(lambda: _peer_is_up(PEER_PORT), timeout=10)
        if not found:
            raise RuntimeError("peer subprocess did not expose its HTTP port in time")

        cls.caller_app = TemporaryApplication(CALLER_APPLICATION).open()
        cls.addClassCleanup(cls.caller_app.close)
        cls.framework = Framework()
        cls.framework.init(cls.caller_app.yml_path)
        cls.addClassCleanup(cls.framework.stop)
        wait_until(lambda: cls.framework.context.get_service_reference("RemoteCall"))

        manager = cls.framework.context.get_service(cls.framework.context.get_service_reference("IManager"))
        from ycappuccino.remote.models.remote_server import RemoteServer

        server = RemoteServer()
        server.id("peer")
        server.host("localhost")
        server.port(PEER_PORT)
        server.scheme("http")
        asyncio.run(manager.up_sert_model(server, subject=None))

    def test_a_call_crosses_the_two_processes(self):
        # calling RemoteCall directly (not through IServiceEndpoint): remote_call's own
        # authorization (secure=True) is exercised by endpoints_service's own tests, not
        # duplicated here -- this test proves the actual cross-process HTTP forwarding.
        remote_call = self.framework.context.get_service(
            self.framework.context.get_service_reference("RemoteCall")
        )

        result = asyncio.run(remote_call.call("POST", ["peer", "greeting"], {}, {"name": "world"}, None))

        self.assertEqual(result.body, {"greeting": "hello world"})

    def test_an_unknown_service_on_the_peer_is_not_found(self):
        remote_call = self.framework.context.get_service(
            self.framework.context.get_service_reference("RemoteCall")
        )

        with self.assertRaises(Exception):
            asyncio.run(remote_call.call("POST", ["peer", "unknown"], {}, {}, None))


if __name__ == "__main__":
    unittest.main()
