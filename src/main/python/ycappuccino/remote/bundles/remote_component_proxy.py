"""
component that represent a remote Component Proxy that is link to n server instance
TODO implementation Laod balancing
"""

import json
import logging
from functools import partial

from ycappuccino.api.service_comm.api import IRemoteComponentProxy

_logger = logging.getLogger(__name__)


class RemoteComponentProxy(IRemoteComponentProxy):
    """component that allow to call a remote client. implemtation of proxy component that call remote client"""

    def __init__(
        self, a_log, a_remote_client, a_specifications, a_properties, a_methods
    ):
        super().__init__()
        self._log = a_log
        self._remote_client = a_remote_client
        self._methods = a_methods
        self._specifications = a_specifications
        self._properties = a_properties
        self._properties_id = json.dumps(a_properties)
        for method in self._methods:
            if (
                not hasattr(self, method)
                and method is not None
                and not method.startswith("_")
            ):
                setattr(self, method, partial(call, service=self, name=method))

    def get_specifications(self):
        return self._specifications

    def get_properties_id(self):
        return self._properties_id


def call(*args, **kwds):
    """
    This method gets called before a method is called.
    """
    service = kwds["service"]
    name = kwds["name"]
    w_kwds = kwds.copy()
    del w_kwds["service"]

    w_kwds["specifications"] = service.get_specifications()
    w_kwds["properties_id"] = service.get_properties_id()

    rval = service._remote_client.method_call(*args, **w_kwds)
    return rval
