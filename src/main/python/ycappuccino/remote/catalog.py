"""
ServiceCatalog: where an interface can be called, with which signatures, on which host:port.

refresh_peer(peer_id) reads the "descriptors" of that RemoteServer's __remote_capabilities__ (signed
when the RemoteServer has a secret, so the peer describes every dispatchable method, not only the
@rpc_method ones) and replaces the ServiceDescriptors stored for it. refresh_all() does it for every
registered peer, keeping the last known descriptors of a peer that cannot be reached; start() runs it
in the background. locate(specification) lists this instance first when it provides the interface
itself, then every registered peer known to expose it.

Nothing is shared between instances: each keeps the descriptors of its peers in its own storage.
"""

import asyncio
import logging
import threading
from typing import Callable

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.api.endpoints_storage import NotFound
from ycappuccino.api.storage import IManager
from ycappuccino.core.framework import Framework
from ycappuccino.remote._http import DEFAULT_TIMEOUT, REMOTE_SERVER_ITEM_ID, call_peer
from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME, describe_components
from ycappuccino.remote.models.service_descriptor import (
    SERVICE_DESCRIPTOR_ITEM_ID,
    ServiceDescriptor,
    descriptor_id,
)

_logger = logging.getLogger(__name__)

_ALL = 10000


def _default_local_descriptors() -> list:
    return describe_components(Framework.get_framework().list_components(), public_only=False)


class ServiceCatalog(YCappuccinoComponent):

    def __init__(
        self,
        manager: IManager,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Callable | None = None,
        local_descriptors: Callable | None = None,
    ) -> None:
        # opener/local_descriptors default to real HTTP and the running Framework; a unit test fakes both
        self._manager = manager
        self._timeout = timeout
        self._opener = opener
        self._local_descriptors = local_descriptors if local_descriptors is not None else _default_local_descriptors

    async def start(self) -> None:
        # peers may be slow or down: never hold this component's validation on them
        threading.Thread(target=self._bootstrap, name="ServiceCatalog-bootstrap", daemon=True).start()

    def _bootstrap(self) -> None:
        try:
            asyncio.run(self.refresh_all())
        except Exception:
            _logger.exception("ServiceCatalog: initial refresh failed")

    async def stop(self) -> None:
        pass

    async def refresh_all(self) -> None:
        for peer in await self._manager.get_many(REMOTE_SERVER_ITEM_ID, {"limit": _ALL}, subject=None):
            document = peer.get_storage_model()
            try:
                await self._refresh(document)
            except Exception:
                _logger.warning("could not refresh the descriptors of remote server %r", document.get("_id"), exc_info=True)

    async def refresh_peer(self, peer_id: str) -> list[str]:
        peer = await self._manager.get_one(REMOTE_SERVER_ITEM_ID, peer_id, subject=None)
        if peer is None:
            raise NotFound(f"unknown remote server {peer_id}")
        return await self._refresh(peer.get_storage_model())

    async def _refresh(self, document: dict) -> list[str]:
        peer_id = document["_id"]
        result = call_peer(
            document, CAPABILITIES_SERVICE_NAME, "GET", [], {}, None, timeout=self._timeout, opener=self._opener,
        )
        received = result.body.get("descriptors", []) if isinstance(result.body, dict) else []
        await self._manager.delete_many(SERVICE_DESCRIPTOR_ITEM_ID, {"peer_id": peer_id}, subject=None)
        for entry in received:
            descriptor = ServiceDescriptor()
            descriptor.id(descriptor_id(peer_id, entry["specification"]))
            descriptor.peer_id(peer_id)
            descriptor.specification(entry["specification"])
            descriptor.methods(entry["methods"])
            await self._manager.up_sert_model(descriptor, subject=None)
        return [entry["specification"] for entry in received]

    async def locate(self, specification: str) -> list[dict]:
        located = [
            {"peer_id": "", "methods": entry["methods"]}
            for entry in self._local_descriptors()
            if entry["specification"] == specification
        ]
        descriptors = await self._manager.get_many(
            SERVICE_DESCRIPTOR_ITEM_ID,
            {"filter": {"specification": specification}, "sort": {"peer_id": 1}, "limit": _ALL},
            subject=None,
        )
        for descriptor in descriptors:
            stored = descriptor.get_storage_model()
            peer = await self._manager.get_one(REMOTE_SERVER_ITEM_ID, stored["peer_id"], subject=None)
            if peer is None:
                continue
            server = peer.get_storage_model()
            located.append({
                "peer_id": stored["peer_id"],
                "host": server["host"],
                "port": server["port"],
                "scheme": server["scheme"],
                "methods": stored["methods"],
            })
        return located
