"""
signatures.describe_interface: the JSON description of what an interface lets a caller invoke.
"""

import json
import unittest
from abc import ABC, abstractmethod

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.api.decorators import rpc_method
from ycappuccino.api.endpoints_service import ServiceResult
from ycappuccino.remote.signatures import describe_interface, type_name


class IGreeter(YCappuccinoComponent, ABC):

    @rpc_method(method="POST", path="/{language}", summary="greet", secure=False)
    @abstractmethod
    async def greet(self, language: str, who: list[str], subject: dict | None = None) -> ServiceResult:
        """public"""

    @abstractmethod
    async def reset(self, force) -> None:
        """internal"""

    def bind(self, service: YCappuccinoComponent) -> None:
        pass

    def _helper(self) -> None:
        pass


class TestTypeName(unittest.TestCase):

    def test_names(self):
        self.assertEqual(type_name(str), "str")
        self.assertEqual(type_name(None), "None")
        self.assertEqual(type_name(type(None)), "None")
        self.assertEqual(type_name(dict | None), "dict | None")
        self.assertEqual(type_name(list[str]), "list[str]")
        self.assertEqual(type_name(ServiceResult), "ycappuccino.api.endpoints_service.ServiceResult")


class TestDescribeInterface(unittest.TestCase):

    def test_a_peer_sees_every_callable_method(self):
        methods = describe_interface(IGreeter, public_only=False)

        self.assertEqual(
            methods,
            [
                {
                    "name": "greet",
                    "params": {"language": "str", "who": "list[str]"},
                    "return_type": "ycappuccino.api.endpoints_service.ServiceResult",
                    "rpc": {"method": "POST", "path": "/{language}", "summary": "greet", "secure": False},
                },
                {"name": "reset", "params": {"force": "Any"}, "return_type": "None", "rpc": None},
            ],
        )
        json.dumps(methods)

    def test_anyone_else_only_sees_the_rpc_methods(self):
        self.assertEqual([method["name"] for method in describe_interface(IGreeter, public_only=True)], ["greet"])


if __name__ == "__main__":
    unittest.main()
