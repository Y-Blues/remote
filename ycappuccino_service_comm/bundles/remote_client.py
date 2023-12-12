#app="all"
from pelix.ipopo.constants import use_ipopo

from ycappuccino_core.api import IActivityLogger, IConfiguration, YCappuccino, IServerProxy, IService
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Property, Provides, Instantiate, BindField, UnbindField
import jsonrpclib
from ycappuccino_core.decorator_app import Layer
from ycappuccino_service_comm.api import IRemoteClient

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

    def ask_service(self):
        list_service_descriptions = self.client.ask_service()
        # create list of proxy service
        for service_descriptions in list_service_descriptions:
            with use_ipopo(self._context) as ipopo:
                # use the iPOPO core service with the "ipopo" variable
                w_id = service_descriptions["id"]
                self._log.info("create remote component proxy {}".format(service_descriptions))
                ipopo.instantiate("RemoteComponentProxy-Factory", "RemoteComponentProxy-Factory-{}".format(w_id),
                                  {
                                      "client_name": self._name,
                                  })

                self._log.info("end remote component proxy {}".format(service_descriptions))





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
