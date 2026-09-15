"""
Registers the "peer" RemoteServer and calls it once at start, to show a real cross-process
IExposedService call. See remote/README.md; run example/peer/ first (port 18160).
"""

import logging

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.api.storage import IManager
from ycappuccino.remote.call import RemoteCall
from ycappuccino.remote.models.remote_server import RemoteServer

_logger = logging.getLogger(__name__)


class RemoteBootstrap(YCappuccinoComponent):

    def __init__(self, manager: IManager, remote_call: RemoteCall):
        self._manager = manager
        self._remote_call = remote_call

    async def stop(self):
        pass

    async def start(self):
        server = RemoteServer()
        server.id("peer")
        server.host("localhost")
        server.port(18160)
        server.scheme("http")
        await self._manager.up_sert_model(server, subject=None)

        result = await self._remote_call.call("POST", ["peer", "greeting"], {}, {"name": "world"}, None)
        _logger.info("remote call result: %s", result.body)
