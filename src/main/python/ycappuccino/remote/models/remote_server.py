"""
RemoteServer: registry entry for a known peer YCappuccino instance, called by RemoteCall over
plain HTTP. Managed manually via /api/crud/remote-servers (see spec: no discovery/heartbeat).
"""

from ycappuccino.api.decorators import Item, Property
from ycappuccino.api.models import Model
from ycappuccino.core.decorator_app import App


@App(name="ycappuccino_remote")
@Item(
    collection="remote_servers", name="remoteServer", plural="remote-servers",
    secure_read=True, secure_write=True,
)
class RemoteServer(Model):

    def __init__(self, a_dict: dict | None = None) -> None:
        super().__init__(a_dict)
        self._host = None
        self._port = None
        self._scheme = None

    @Property(name="host")
    def host(self, a_value: str) -> None:
        self._host = a_value

    @Property(name="port", type="integer", minimum=1, maximum=65535)
    def port(self, a_value: int) -> None:
        self._port = a_value

    @Property(name="scheme")
    def scheme(self, a_value: str) -> None:
        self._scheme = a_value
