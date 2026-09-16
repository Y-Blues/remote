"""
The single most important test of the 2026-09-16 second addendum: a component in "container A"
depends on IInventoryService -- an ARBITRARY, application-defined interface, never part of remote's
own vocabulary, that exists ONLY in "container B" (a real subprocess) -- gets a dynamically-created
proxy for it (resolved from container B's widened __remote_capabilities__ "components", synthesized
on the fly by remote_proxy.make_generic_proxy, dispatched on B's side by dispatch.RemoteDispatch),
and successfully calls check_stock() on it, getting back the REAL result computed in container B,
never locally (container A has no implementation of IInventoryService of its own at all).

This also proves, for real, the timing-hazard resolution the design doc addendum lands on -- option
(b), not (a), see component_directory.py's module docstring: InventoryConsumer, in container A,
declares its dependency on IInventoryService as `list[IInventoryService]` (an AGGREGATE, always
"available" -- as an initially empty list -- however late its providers show up), never a plain
required constructor parameter. Right after Framework.init() returns, no RemoteServer is registered
yet, so ComponentDirectory's own start() (deferred to a background thread, see its module docstring)
has nothing to discover: the single test method below asserts the list really is empty at that point
first, proving the hazard is real, not a strawman -- only once a RemoteServer pointing at container B
is registered does ComponentDirectory react (synchronously this time: an ITrigger reaction, not a
nested call from its own start()/stop(), which core's README says plainly is safe) and, through
iPOPO's own bind() machinery (unrelated to remote -- the same mechanism any late-arriving
optional/aggregate dependency already relies on), does InventoryConsumer's list become non-empty.

Container B runs in a genuine subprocess (only one Pelix framework per process, see
test_remote_framework.py's own docstring for why). Container A runs in this test process. Container
A must be able to import the SAME IInventoryService module container B publishes the qualified path
of: resolve_class() only works if the module is importable wherever it runs, an inherent requirement
of a fully generic, zero-hardcoded-interface-list mechanism (see design doc addendum part C) -- in
this test, sharing container B's own temp app root on sys.path is the simplest way to get that; in a
real deployment, this is normally a small shared "contracts" package both sides install.
"""

import asyncio
import json
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

from ycappuccino.core.framework import Framework
from ycappuccino.core.testing import TemporaryApplication, wait_until

CONTAINER_B_PORT = 18162

CONTAINER_B_APPLICATION = {
    "conf/application.yml": f"""
        name: componentdirectorycontainerb
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
    "PACKAGE/inventory.py": """
        import abc

        from ycappuccino.api.core_base import YCappuccinoComponent


        class IInventoryService(YCappuccinoComponent):
            @abc.abstractmethod
            async def check_stock(self, sku: str) -> int:
                \"\"\"units of `sku` currently in stock\"\"\"


        class InventoryService(IInventoryService):
            # a real, deterministic computation only container B can perform -- proves the result
            # the test asserts on was actually computed there, never guessed/faked locally
            def __init__(self):
                pass

            async def check_stock(self, sku: str) -> int:
                return len(sku) * 7

            async def start(self):
                pass

            async def stop(self):
                pass
    """,
}


def _container_b_is_up(port):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/services/__remote_capabilities__", timeout=1):
            return True
    except (urllib.error.URLError, ConnectionError):
        return False


class TestComponentDirectoryAcrossTwoContainers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.container_b_app = TemporaryApplication(CONTAINER_B_APPLICATION).open()
        cls.addClassCleanup(cls.container_b_app.close)
        # see module docstring: container A (this test process) must be able to import the exact
        # same IInventoryService module container B's qualified path names
        sys.path.insert(0, cls.container_b_app.root)
        cls.container_b_process = subprocess.Popen(
            [sys.executable, "-m", "ycappuccino.core.runner", "--root_path", cls.container_b_app.root],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.addClassCleanup(cls.container_b_process.wait, 5)
        cls.addClassCleanup(cls.container_b_process.terminate)
        found = wait_until(lambda: _container_b_is_up(CONTAINER_B_PORT), timeout=10)
        if not found:
            raise RuntimeError("container B subprocess did not expose its HTTP port in time")

        container_a_application = {
            "conf/application.yml": """
                name: componentdirectorycontainera
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
            "PACKAGE/consumer.py": f"""
                from {cls.container_b_app.package}.inventory import IInventoryService
                from ycappuccino.api.core_base import YCappuccinoComponent


                class InventoryConsumer(YCappuccinoComponent):
                    # an AGGREGATE (list) dependency: always "available" (as [] until a provider
                    # shows up) -- the operational contract this addendum requires for any
                    # dynamically-federated interface, see component_directory.py
                    def __init__(self, inventories: list[IInventoryService]):
                        self._inventories = inventories

                    async def start(self):
                        pass

                    async def stop(self):
                        pass

                    def has_inventory(self):
                        return bool(self._inventories)

                    async def check(self, sku):
                        return await self._inventories[0].check_stock(sku)
            """,
        }
        cls.container_a_app = TemporaryApplication(container_a_application).open()
        cls.addClassCleanup(cls.container_a_app.close)
        cls.framework = Framework()
        cls.framework.init(cls.container_a_app.yml_path)
        cls.addClassCleanup(cls.framework.stop)
        wait_until(lambda: cls.framework.context.get_service_reference("InventoryConsumer"))

    def test_the_dynamic_proxy_is_created_on_the_fly_and_the_late_arriving_consumer_gets_the_real_result(self):
        consumer = self.framework.context.get_service(
            self.framework.context.get_service_reference("InventoryConsumer")
        )
        # right after boot: no RemoteServer registered yet, ComponentDirectory has discovered
        # nothing, no proxy for IInventoryService exists -- the aggregate dependency is empty,
        # proving the timing hazard described in component_directory.py is real, not a strawman
        self.assertFalse(consumer.has_inventory())

        manager = self.framework.context.get_service(self.framework.context.get_service_reference("IManager"))
        from ycappuccino.remote.models.remote_server import RemoteServer

        server = RemoteServer()
        server.id("b")
        server.host("localhost")
        server.port(CONTAINER_B_PORT)
        server.scheme("http")
        asyncio.run(manager.up_sert_model(server, subject=None))

        # registering the peer synchronously drives ComponentDirectory.execute() (an ITrigger
        # reaction, not a call from its own start()/stop() -- see module docstring for why that is
        # safe), which discovers IInventoryService on container B and instantiates a real, running
        # dynamic proxy for it; iPOPO's own bind() then delivers it into InventoryConsumer's list
        found = wait_until(lambda: consumer.has_inventory(), timeout=5)
        self.assertTrue(found, "the dynamic proxy for IInventoryService never bound to InventoryConsumer")

        result = asyncio.run(consumer.check("widget"))

        # len("widget") * 7 == 42, computed by InventoryService.check_stock in container B, never
        # locally: container A has no implementation of IInventoryService of its own whatsoever
        self.assertEqual(result, 42)

    def test_remote_capabilities_reports_the_qualified_path_over_http(self):
        with urllib.request.urlopen(
            f"http://localhost:{CONTAINER_B_PORT}/api/services/__remote_capabilities__", timeout=5
        ) as response:
            payload = json.loads(response.read())

        provides = {
            qualified_path
            for component in payload["data"]["components"]
            for qualified_path in component["provides"]
        }
        self.assertIn(f"{self.container_b_app.package}.inventory.IInventoryService", provides)

    def test_remote_dispatch_wire_shape_directly_over_http(self):
        qualified_path = f"{self.container_b_app.package}.inventory.IInventoryService"
        url = f"http://localhost:{CONTAINER_B_PORT}/api/services/__remote_dispatch__/{qualified_path}/check_stock"
        body = json.dumps({"kwargs": {"sku": "abcd"}}).encode()
        request = urllib.request.Request(
            url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())

        self.assertEqual(payload["data"], {"result": 28})


if __name__ == "__main__":
    unittest.main()
