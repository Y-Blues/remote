"""
Fixtures shared by the remote tests: a real Manager on MemoryStorage, no framework.
"""

import os
import tempfile

from ycappuccino.storage.files import LocalFileStore
from ycappuccino.storage.items import ItemManager
from ycappuccino.storage.manager import Manager
from ycappuccino.storage.memory import MemoryStorage

# importing the model registers it with ItemManager
from ycappuccino.remote.models import remote_server  # noqa: F401


def create_manager():
    """manager on a memory storage; the caller removes the returned directory"""
    directory = tempfile.mkdtemp()
    manager = Manager(MemoryStorage(), ItemManager(), [], [], LocalFileStore(os.path.join(directory, "files")))
    return manager, directory
