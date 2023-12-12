#app="all"
from pelix.ipopo.constants import use_ipopo
from ycappuccino_core.api import IActivityLogger, IConfiguration, YCappuccino, IServerProxy, IService
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Property, Provides, Instantiate, BindField, UnbindField
import jsonrpclib
from ycappuccino_core.decorator_app import Layer

from ycappuccino_service_comm.api import IRemoteClientFactory, IRemoteClient

_logger = logging.getLogger(__name__)

@ComponentFactory('RemoteClientFactory-Factory')
@Provides(specifications=[IRemoteClientFactory.name, IService.name, YCappuccino.name])
@Requires("_log", IActivityLogger.name, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.name)
@Requires("_list_remote_client", IRemoteClient.name, aggregate=True,optional=True)
@Instantiate("RemoteClientFactory")
@Layer(name="ycappuccino_service_comm")
class RemoteClientFactory(IRemoteClientFactory, IService):

    def __init__(self):
        super().__init__()
        self._log = None
        self._context = None
        self._list_remote_client = []
        self._map_remote_client = {}

    def create_remote_client(self, a_remote_server):
        with use_ipopo(self._context) as ipopo:
            w_id = self.get_remote_server_id(a_remote_server)
            # use the iPOPO core service with the "ipopo" variable
            self._log.info("create remote client  {}".format(a_remote_server))
            ipopo.instantiate("Manager-Proxy-Factory", "Manager-Proxy-{}".format(w_id),
                              {
                                "remote_server_id": w_id,
                                "name": w_id,
                                "host": a_remote_server.get_host(),
                                "port": a_remote_server.get_port(),
                                "scheme": a_remote_server.get_scheme()
                               })

            self._log.info("end create remote client  {}".format(a_remote_server))

    def remove_remote_client(self, a_remote_server):
        with use_ipopo(self._context) as ipopo:
            w_id = self.get_remote_server_id(a_remote_server)
            # use the iPOPO core service with the "ipopo" variable
            self._log.info("remove remote client  {}".format(a_remote_server))
            ipopo.kill(w_id)

            self._log.info("end remove remote client  {}".format(a_remote_server))

    def get_remote_server_id(self,a_remote_server):
        return a_remote_server["id"]

    @BindField("_list_remote_client")
    def bind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        try:
            a_remote_client.test()
            self._map_remote_client[w_id] = a_remote_client
        except:
            self.remove_remote_client(a_remote_client)


    @UnbindField("_list_remote_client")
    def unbind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        del self._map_remote_client[w_id]


    def get_list_remote_client(self):
        return self._map_remote_client

    def get_name(self):
        return "remove_call"

    def is_secure(self):
        return False

    def post(self, a_header, a_url_path, a_body):
        """ return tuple of 2 element that admit a dictionnary of header and a body"""
        w_id = a_body["id"]
        w_param = a_body["params"]
        self._map_remote_client[w_id].execute(w_param)
        return None, None

    @Validate
    def validate(self, context):
        self._log.info("RemoteClient validating")


        self._log.info("RemoteClient validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteClient invalidating")

        self._log.info("RemoteClient invalidated")
