"""
Fixtures shared by the remote tests: a real Manager on MemoryStorage, no framework, and fakes for
IExposedService/IAuthorization (same shape as endpoints_service's own service_fixtures.py, kept
independent since remote's production code must not depend on endpoints_service, see spec section 6).
"""

import os
import tempfile

from ycappuccino.api.endpoints_service import IExposedService, ServiceResult
from ycappuccino.api.endpoints_storage import IAuthorization
from ycappuccino.storage.files import LocalFileStore
from ycappuccino.storage.items import ItemManager
from ycappuccino.storage.manager import Manager
from ycappuccino.storage.memory import MemoryStorage

# importing the model registers it with ItemManager
from ycappuccino.remote.models import remote_server  # noqa: F401

ALICE = {"sub": "alice", "tid": "acme"}


def create_manager():
    """manager on a memory storage; the caller removes the returned directory"""
    directory = tempfile.mkdtemp()
    manager = Manager(MemoryStorage(), ItemManager(), [], [], LocalFileStore(os.path.join(directory, "files")))
    return manager, directory


class FakeExposedService(IExposedService):

    def __init__(self, name, secure=True):
        self.name = name
        self.secure = secure
        self.calls = []
        self.error = None
        self.result = ServiceResult(body={"ok": True})

    async def call(self, method, extra_path, params, body, subject):
        self.calls.append((method, extra_path, params, body, subject))
        if self.error is not None:
            raise self.error
        return self.result

    async def start(self):
        pass

    async def stop(self):
        pass


class FakeAuthorization(IAuthorization):
    """authorizes the (action, resource) pairs of allowed; "*" authorizes everything"""

    def __init__(self, allowed=("*",)):
        self.allowed = set(allowed)
        self.calls = []

    async def is_authorized(self, subject, action, item_id):
        self.calls.append((subject["sub"], action, item_id))
        return "*" in self.allowed or (action, item_id) in self.allowed

    async def start(self):
        pass

    async def stop(self):
        pass
