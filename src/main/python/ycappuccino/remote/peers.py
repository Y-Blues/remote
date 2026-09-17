"""
IPeers: a source of the peers an instance knows, each a RemoteServer-shaped document:

    {"_id": "storage", "scheme": "http", "host": "localhost", "port": 8201, "secret": "..."}

A peer without scheme/host/port is only known to authenticate its signed calls (a process without an HTTP
server, a terminal frontend for example): nothing is ever asked of it. Sources: StoredPeers (RemoteServer
items, stored_peers.py) and ConfiguredPeers (the application's configuration, configured_peers.py);
ComponentDirectory and PeerHmacAuthentication read every published source.
"""

from abc import ABC, abstractmethod

from ycappuccino.api.core_base import YCappuccinoComponent


class IPeers(YCappuccinoComponent, ABC):

    @abstractmethod
    async def all(self) -> list[dict]:
        """every peer of this source"""

    @abstractmethod
    async def get(self, peer_id: str) -> dict | None:
        """the peer with this id, or None"""


def has_address(peer: dict) -> bool:
    return all(peer.get(key) for key in ("scheme", "host", "port"))
