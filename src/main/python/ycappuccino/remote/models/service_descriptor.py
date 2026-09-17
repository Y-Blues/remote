"""
ServiceDescriptor: what a peer lets this instance call -- one interface it provides and the signatures
of its methods (signatures.describe_interface) -- as last read from that peer's __remote_capabilities__
by ServiceCatalog (catalog.py). peer_id is the id of the RemoteServer to call it through (its host,
port and scheme). Each instance keeps its own descriptors in its own storage: nothing is shared.
"""

from ycappuccino.api.decorators import Item, Property
from ycappuccino.api.models import Model
from ycappuccino.core.decorator_app import App

SERVICE_DESCRIPTOR_ITEM_ID = "serviceDescriptor"


def descriptor_id(peer_id: str, specification: str) -> str:
    return f"{peer_id}:{specification}"


@App(name="ycappuccino_remote")
@Item(
    collection="service_descriptors", name=SERVICE_DESCRIPTOR_ITEM_ID, plural="service-descriptors",
    secure_read=True, secure_write=True,
)
class ServiceDescriptor(Model):

    def __init__(self, a_dict: dict | None = None) -> None:
        super().__init__(a_dict)
        self._peer_id = None
        self._specification = None
        self._methods = None

    @Property(name="peer_id")
    def peer_id(self, a_value: str) -> None:
        self._peer_id = a_value

    @Property(name="specification")
    def specification(self, a_value: str) -> None:
        self._specification = a_value

    @Property(name="methods", type="array")
    def methods(self, a_value: list) -> None:
        self._methods = a_value
