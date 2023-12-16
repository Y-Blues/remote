"""
component that represent a remote Component Proxy that is link to n server instance
TODO implementation Laod balancing
"""

from ycappuccino_api.core.api import IActivityLogger, IConfiguration
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Property, Provides
from ycappuccino_core.decorator_app import Layer
from ycappuccino_api.service_comm.api import IRemoteComponentProxy

_logger = logging.getLogger(__name__)

@ComponentFactory('RemoteComponentProxy-Factory')
@Provides(specifications=[IRemoteComponentProxy.name])
@Requires("_log", IActivityLogger.name, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.name)
@Property('_client_name',"client_name","")
@Layer(name="ycappuccino_service_comm")
class RemoteComponentProxy(IRemoteComponentProxy):
    """ component that allow to call a remote client. implemtation of proxy component that call remote client"""
    def __init__(self):
        super().__init__()
        self._log = None
        self._client_name = None
        self._list_remote_client = None

    def execute(self, a_params):
        """ return tuple of 2 element that admit a dictionnary of header and a body
        TODO """
        self._list_remote_client[0].call(a_params)

    def add_client(self, a_client):
        self._list_remote_client.append(a_client)

    @Validate
    def validate(self, context):
        self._log.info("RemoteComponentProxy validating")
        self._log.info("RemoteComponentProxy validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteComponentProxy invalidating")

        self._log.info("RemoteComponentProxy invalidated")
