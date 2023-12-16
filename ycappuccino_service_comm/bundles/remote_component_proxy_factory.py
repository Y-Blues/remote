"""
component that create a component and assign remote client
"""
from pelix.ipopo.constants import use_ipopo
from ycappuccino_api.core.api import IActivityLogger, IConfiguration
import logging
from pelix.ipopo.decorators import ComponentFactory, Requires, Validate, Invalidate, Provides, BindField, UnbindField
from ycappuccino_core.decorator_app import Layer
from ycappuccino_api.service_comm.api import IRemoteClient, IRemoteComponentProxy, IRemoteComponentProxyFactory

_logger = logging.getLogger(__name__)

@ComponentFactory('IRemoteComponentProxyFactory-Factory')
@Provides(specifications=[IRemoteComponentProxyFactory.name])
@Requires("_log", IActivityLogger.name, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.name)
@Requires("_list_remote_proxy", IRemoteComponentProxy.name)
@Requires("_list_remote_client", IRemoteClient.name)
@Layer(name="ycappuccino_service_comm")
class RemoteComponentProxyFactory(IRemoteComponentProxyFactory):
    """ component that allow to call a remote client. implemtation of proxy component that call remote client"""
    def __init__(self):
        super().__init__()
        self._log = None
        self._list_remote_proxy = None
        self._list_remote_client = None

        self._map_remote_proxy = {}
        self._map_remote_client = {}

    def ask_service(self,  a_client):
        """
        retreive service provide by the client and create a remote proxy if not exists else assign client to it
        """
        list_service_descriptions = a_client.ask_service()
        # create list of proxy service
        for service_descriptions in list_service_descriptions:
            with use_ipopo(self._context) as ipopo:
                # use the iPOPO core service with the "ipopo" variable
                w_id = service_descriptions["id"]
                if w_id in self._map_remote_proxy.keys():
                    self._map_remote_proxy[w_id].add_client(a_client)
                else:
                    self._log.info("create remote component proxy {}".format(service_descriptions))
                    ipopo.instantiate("RemoteComponentProxy-Factory", "RemoteComponentProxy-Factory-{}".format(w_id),
                                      {
                                          "client_name": self._name,
                                      })

                    self._log.info("end remote component proxy {}".format(service_descriptions))

    @BindField("_list_remote_client")
    def bind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        self._map_remote_client[w_id]=a_remote_client

    @UnbindField("_list_remote_client")
    def unbind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        del self._map_remote_client[w_id]

    @BindField("_list_remote_proxy")
    def bind_remote_proxy(self, field, a_remote_proxy, a_service_reference):
        w_id = a_remote_proxy.get_name()
        self._map_remote_proxy[w_id] = a_remote_proxy

    @UnbindField("_list_remote_proxy")
    def unbind_remote_proxy(self, field, a_remote_proxy, a_service_reference):
        w_id = a_remote_proxy.get_name()
        del self._map_remote_proxy[w_id]

    @Validate
    def validate(self, context):
        self._log.info("RemoteComponentProxy validating")
        self._log.info("RemoteComponentProxy validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteComponentProxy invalidating")

        self._log.info("RemoteComponentProxy invalidated")
