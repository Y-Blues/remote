#app="all"

from ycappuccino_core.api import IActivityLogger, IConfiguration, YCappuccino, IServerProxy, IService
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Property, Provides, Instantiate, BindField, UnbindField
import jsonrpclib
from ycappuccino_core.decorator_app import Layer
from ycappuccino_service_comm.api import IRemoteClient, IRemoteComponentProxy

_logger = logging.getLogger(__name__)

@ComponentFactory('RemoteComponentProxy-Factory')
@Provides(specifications=[IRemoteComponentProxy.name])
@Requires("_log", IActivityLogger.name, spec_filter="'(name=main)'")
@Requires("_list_remote_client", IRemoteClient.name, optional=True, aggregate=True)
@Requires("_config", IConfiguration.name)
@Property('_client_name',"client_name","")
@Layer(name="ycappuccino_service_comm")
class RemoteComponentProxy(IRemoteComponentProxy):

    def __init__(self):
        super().__init__()
        self._log = None
        self._client = None
        self._client_name = None
        self._list_remote_client = None

    def execute(self, a_params):
        """ return tuple of 2 element that admit a dictionnary of header and a body"""
        self._client.call(a_params)

    @BindField("_list_remote_client")
    def bind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        if w_id == self._client_name:
            self._client = a_remote_client

    @UnbindField("_list_remote_client")
    def unbind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        if w_id == self._client_name:
            self._client = None

    @Validate
    def validate(self, context):
        self._log.info("RemoteComponentProxy validating")
        self._log.info("RemoteComponentProxy validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteComponentProxy invalidating")

        self._log.info("RemoteComponentProxy invalidated")
