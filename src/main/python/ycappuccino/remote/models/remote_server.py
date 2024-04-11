from ycappuccino.core.models.decorators import Property
from ycappuccino.storage.models.model import Model

"""
represent a server on which we can connect
"""


class RemoteServer(Model):
    def __init__(self, a_dict=None):
        super().__init__(a_dict)
        self._host = None
        self._port = None
        self._scheme = None

    @Property(name="scheme")
    def scheme(self, a_value):
        self._scheme = a_value

    @Property(name="host")
    def host(self, a_value):
        self._host = a_value

    @Property(name="port")
    def port(self, a_value):
        self._port = a_value

    def get_scheme(self):
        return self._scheme

    def get_host(self):
        return self._host

    def get_port(self):
        return self._port
