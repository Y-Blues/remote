"""
component that create remote client component or remove remote client. it allow list all of them
"""

from pelix.ipopo.constants import use_ipopo
from ycappuccino.api.core.api import IActivityLogger, IConfiguration, IService
import logging
from pelix.ipopo.decorators import (
    ComponentFactory,
    Requires,
    Validate,
    Invalidate,
    Provides,
    Instantiate,
    BindField,
    UnbindField,
)

from ycappuccino.core.decorator_app import Layer

from ycappuccino.api.remote.api import IRemoteClientFactory, IRemoteClient
from ycappuccino.remote.bundles.remote_client import connect

_logger = logging.getLogger(__name__)


@ComponentFactory("RemoteClientFactory-Factory")
@Provides(specifications=[IRemoteClientFactory.__name__, IService.__name__])
@Requires("_log", IActivityLogger.__name__, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.__name__)
@Requires("_list_remote_client", IRemoteClient.__name__, aggregate=True, optional=True)
@Instantiate("RemoteClientFactory")
@Layer(name="ycappuccino_service_comm")
class RemoteClientFactory(IRemoteClientFactory, IService):

    def __init__(self):
        super(IRemoteClientFactory, self).__init__()
        super(IService, self).__init__()
        self._log = None
        self._context = None
        self._list_remote_client = []
        self._map_remote_client = {}

    def create_remote_client(self, a_remote_server):
        with use_ipopo(self._context) as ipopo:
            w_id = self.get_remote_server_id(a_remote_server)
            if (
                connect(
                    a_remote_server.get_scheme(),
                    a_remote_server.get_host(),
                    a_remote_server.get_port(),
                )
                is not None
            ):

                # use the iPOPO core service with the "ipopo" variable
                self._log.info("create remote client  {}".format(a_remote_server))
                ipopo.instantiate(
                    "RemoteClient-Factory",
                    "RemoteClient-Factory-{}".format(w_id),
                    {
                        "remote_client_id": w_id,
                        "name": w_id,
                        "host": a_remote_server.get_host(),
                        "port": a_remote_server.get_port(),
                        "scheme": a_remote_server.get_scheme(),
                    },
                )
                print("create remote client  {}".format(a_remote_server._id))
                self._log.info("end create remote client  {}".format(a_remote_server))
                return True
            return False

    def remove_remote_client(self, a_id):
        try:
            with use_ipopo(self._context) as ipopo:
                # use the iPOPO core service with the "ipopo" variable
                self._log.info("remove remote client  {}".format(a_id))
                # ipopo.kill("RemoteClient-Factory-{}".format(a_id))

                self._log.info("end remove remote client  {}".format(a_id))
        except:
            pass

    @staticmethod
    def get_remote_server_id(a_remote_server):
        return a_remote_server._id

    @BindField("_list_remote_client")
    def bind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        try:
            if a_remote_client.is_active():
                a_remote_client.test()
                self._map_remote_client[w_id] = a_remote_client
        except:
            self.remove_remote_client(w_id)

    @UnbindField("_list_remote_client")
    def unbind_remote_client(self, field, a_remote_client, a_service_reference):
        w_id = a_remote_client.get_name()
        # del self._map_remote_client[w_id]

    def get_list_remote_client(self):
        return self._map_remote_client

    def get_name(self):
        return "remove_call"

    def is_secure(self):
        return False

    def post(self, a_header, a_url_path, a_body):
        """return tuple of 2 element that admit a dictionnary of header and a body"""
        w_id = a_body["id"]
        w_param = a_body["params"]
        self._map_remote_client[w_id].execute(w_param)
        return None, None

    @Validate
    def validate(self, context):
        self._log.info("RemoteClientFactory validating")
        self._context = context

        self._log.info("RemoteClientFactory validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteClientFactory invalidating")
        self._context = None

        self._log.info("RemoteClientFactory invalidated")
