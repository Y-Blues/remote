"""
component that allow to communicate to another server
"""

from ycappuccino_api.core.api import IActivityLogger, IConfiguration, YCappuccino
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Property, Provides
import jsonrpclib
from ycappuccino_core.decorator_app import Layer
from ycappuccino_api.service_comm.api import IRemoteClient

_logger = logging.getLogger(__name__)

@ComponentFactory('RemoteClient-Factory')
@Provides(specifications=[IRemoteClient.name, YCappuccino.name])
@Requires("_log", IActivityLogger.name, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.name)
@Property('_host', "host","localhost")
@Property('_port',"port",8888)
@Property('_scheme',"scheme","http")
@Property('_name',"name","")
@Layer(name="ycappuccino_service_comm")
class RemoteClient(IRemoteClient):

    def __init__(self):
        super().__init__()
        self._log = None
        self._client = None
        self._name = None
        self._host = None
        self._port = None
        self._scheme = None
        self._context = None

    def execute(self, a_params):
        """ return tuple of 2 element that admit a dictionnary of header and a body"""
        self._client.call(a_params)

    def get_name(self):
        return self._name





    @Validate
    def validate(self, context):
        self._log.info("RemoteClient validating")
        self._context = context
        self._client = jsonrpclib.ServerProxy('{}}://{}:{}'.format(self._scheme,self._host,self._port))
        self.ask_service()
        self._log.info("RemoteClient validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteClient invalidating")

        self._log.info("RemoteClient invalidated")
