"""
ConfiguredPeers: peers declared in the application's configuration, for an instance whose storage is itself
a peer, or which has no storage:

    components:
      ConfiguredPeers:
        peers: "storage=http://localhost:8201, frontend"   # id=scheme://host:port, or an id alone
        secret: "the secret shared by these peers"
"""

import urllib.parse

from ycappuccino.remote.peers import IPeers


class ConfiguredPeers(IPeers):

    def __init__(self, peers: str = "", secret: str = "") -> None:
        self._peers = peers
        self._secret = secret

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def all(self) -> list[dict]:
        return [self._parse(entry.strip()) for entry in self._peers.split(",") if entry.strip()]

    async def get(self, peer_id: str) -> dict | None:
        return next((peer for peer in await self.all() if peer["_id"] == peer_id), None)

    def _parse(self, entry: str) -> dict:
        peer_id, _, address = (part.strip() for part in entry.partition("="))
        peer: dict = {"_id": peer_id}
        if address:
            url = urllib.parse.urlsplit(address)
            if not url.scheme or not url.hostname or not url.port:
                raise ValueError(f"peer {peer_id!r}: {address!r} is not scheme://host:port")
            peer.update(scheme=url.scheme, host=url.hostname, port=url.port)
        if self._secret:
            peer["secret"] = self._secret
        return peer
