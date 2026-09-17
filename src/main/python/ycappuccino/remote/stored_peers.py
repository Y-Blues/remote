"""
StoredPeers: the peers registered as RemoteServer items (/api/crud/remote-servers), in this instance's own
storage.
"""

from ycappuccino.api.storage import IManager
from ycappuccino.remote._http import REMOTE_SERVER_ITEM_ID
from ycappuccino.remote.peers import IPeers


class StoredPeers(IPeers):

    def __init__(self, manager: IManager) -> None:
        self._manager = manager

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def all(self) -> list[dict]:
        return [peer.get_storage_model() for peer in await self._manager.get_many(REMOTE_SERVER_ITEM_ID, subject=None)]

    async def get(self, peer_id: str) -> dict | None:
        peer = await self._manager.get_one(REMOTE_SERVER_ITEM_ID, peer_id, subject=None)
        return None if peer is None else peer.get_storage_model()
