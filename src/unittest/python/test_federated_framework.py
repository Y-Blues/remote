"""
The single most important test of this addendum: a component in "container A" depends on
IServiceEndpoint and calls a service that exists only in "container B", using nothing but
FederatedServiceEndpoint (local dispatch + dynamic discovery fallback) and a RemoteServer pointing
at B -- no peer id, no target-service addressing, no "this call is remote" marker anywhere at the
call site. This proves the actual point of the whole addendum end to end, across two real processes.

Container B runs in a genuine subprocess (see test_remote_framework.py's module docstring for why:
only one Pelix framework per process). It loads ycappuccino.endpoints_storage (required by
http_server's own ApiServlet for its unrelated ICrud/IDrafts/IItemCatalog dependencies -- nothing
to do with the IServiceEndpoint choice) and ycappuccino.remote, but NEVER
ycappuccino.endpoints_service alongside ycappuccino.remote (see federated_endpoint.py and
README.md, "ne jamais charger les deux ensemble"): FederatedServiceEndpoint is therefore
unambiguously the sole IServiceEndpoint http_server dispatches every /api/services/* request to,
including its own __remote_capabilities__ probe used below to check readiness.

Container A runs in this test process (ycappuccino.remote only, no endpoints_service, no
http_server -- it never receives HTTP requests itself, only makes them): it registers a
RemoteServer pointing at B, then a plain native component depending on IServiceEndpoint calls
"service_b" exactly as it would call any local service.
"""

import asyncio
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

from ycappuccino.core.framework import Framework
from ycappuccino.core.testing import TemporaryApplication, wait_until

CONTAINER_B_PORT = 18161

CONTAINER_B_APPLICATION = {
    "conf/application.yml": f"""
        name: federatedcontainerb
        bundle_prefix:
          - ycappuccino.storage
          - ycappuccino.endpoints_storage
          - ycappuccino.remote
          - ycappuccino.http_server
          - PACKAGE
        layers:
          ycappuccino_storage_memory:
            active: true
        config:
          http_server:
            active: true
            port: {CONTAINER_B_PORT}
            ip: localhost
          shell:
            console: false
    """,
    "PACKAGE/__init__.py": "",
    "PACKAGE/service_b.py": """
        from ycappuccino.api.endpoints_service import IExposedService, ServiceResult


        class ServiceB(IExposedService):
            name = "service_b"
            secure = False

            def __init__(self):
                pass

            async def call(self, method, extra_path, params, body, subject):
                return ServiceResult(body={"greeting": f"hello {body['name']} from container B"})

            async def start(self):
                pass

            async def stop(self):
                pass
    """,
}

CONTAINER_A_APPLICATION = {
    "conf/application.yml": """
        name: federatedcontainera
        bundle_prefix:
          - ycappuccino.storage
          - ycappuccino.remote
          - PACKAGE
        layers:
          ycappuccino_storage_memory:
            active: true
        config:
          shell:
            console: false
    """,
    "PACKAGE/__init__.py": "",
    "PACKAGE/caller.py": """
        from ycappuccino.api.core_base import YCappuccinoComponent
        from ycappuccino.api.endpoints_service import IServiceEndpoint


        class FederatedCaller(YCappuccinoComponent):
            # A component that only knows IServiceEndpoint and a service name -- exactly what it
            # would know to call a purely local service. No peer id, no remote_call, nothing.
            def __init__(self, endpoint: IServiceEndpoint):
                self._endpoint = endpoint

            async def start(self):
                pass

            async def stop(self):
                pass

            async def call_service_b(self):
                return await self._endpoint.call(
                    "service_b", "POST", [], {}, {"name": "container A"}, None
                )
    """,
}


def _container_b_is_up(port):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/services/__remote_capabilities__", timeout=1):
            return True
    except (urllib.error.URLError, ConnectionError):
        return False


class TestFederatedCallAcrossTwoContainers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.container_b_app = TemporaryApplication(CONTAINER_B_APPLICATION).open()
        cls.addClassCleanup(cls.container_b_app.close)
        cls.container_b_process = subprocess.Popen(
            [sys.executable, "-m", "ycappuccino.core.runner", "--root_path", cls.container_b_app.root],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.addClassCleanup(cls.container_b_process.wait, 5)
        cls.addClassCleanup(cls.container_b_process.terminate)
        found = wait_until(lambda: _container_b_is_up(CONTAINER_B_PORT), timeout=10)
        if not found:
            raise RuntimeError("container B subprocess did not expose its HTTP port in time")

        cls.container_a_app = TemporaryApplication(CONTAINER_A_APPLICATION).open()
        cls.addClassCleanup(cls.container_a_app.close)
        cls.framework = Framework()
        cls.framework.init(cls.container_a_app.yml_path)
        cls.addClassCleanup(cls.framework.stop)
        wait_until(lambda: cls.framework.context.get_service_reference("FederatedCaller"))

        manager = cls.framework.context.get_service(cls.framework.context.get_service_reference("IManager"))
        from ycappuccino.remote.models.remote_server import RemoteServer

        server = RemoteServer()
        server.id("b")
        server.host("localhost")
        server.port(CONTAINER_B_PORT)
        server.scheme("http")
        asyncio.run(manager.up_sert_model(server, subject=None))

    def test_the_call_site_never_names_a_peer_and_still_crosses_the_two_containers(self):
        caller = self.framework.context.get_service(
            self.framework.context.get_service_reference("FederatedCaller")
        )

        result = asyncio.run(caller.call_service_b())

        self.assertEqual(result.body, {"greeting": "hello container A from container B"})

    def test_a_service_absent_from_both_containers_is_not_found(self):
        from ycappuccino.api.endpoints_storage import NotFound

        endpoint = self.framework.context.get_service(
            self.framework.context.get_service_reference("IServiceEndpoint")
        )

        with self.assertRaises(NotFound):
            asyncio.run(endpoint.call("nowhere", "GET", [], {}, None, None))


if __name__ == "__main__":
    unittest.main()
