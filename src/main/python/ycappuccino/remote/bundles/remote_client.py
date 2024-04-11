"""
component that allow to communicate to another server
"""

from ycappuccino.api.core.api import IActivityLogger, IConfiguration
import logging
from pelix.ipopo.decorators import (
    ComponentFactory,
    Requires,
    Validate,
    Invalidate,
    Property,
    Provides,
)
import jsonrpclib

from ycappuccino.core import executor_service
from ycappuccino.core.decorator_app import Layer
from ycappuccino.api.service_comm.api import (
    IRemoteClient,
    IRemoteServer,
    IRemoteClientFactory,
)
from ycappuccino.core.executor_service import Callable

_logger = logging.getLogger(__name__)


def connect(a_scheme, a_host, a_port):
    try:
        client = jsonrpclib.ServerProxy("{}://{}:{}".format(a_scheme, a_host, a_port))
        client.test()
        return client
    except:
        return None


class ValidateAskService(Callable):
    """ """

    def __init__(self, a_service):
        super(ValidateAskService, self).__init__("ValidateaskService")
        self._service = a_service

    def run(self):
        self._service.validate_ask_service()


@ComponentFactory("RemoteClient-Factory")
@Provides(specifications=[IRemoteClient.__name__])
@Requires("_log", IActivityLogger.__name__, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.__name__)
@Property("_host", "host", "localhost")
@Property("_port", "port", 8888)
@Property("_scheme", "scheme", "http")
@Property("_name", "name", "")
@Property("_remote_client_id", "remote_client_id", "")
@Requires("_remote_server", IRemoteServer.__name__)
@Requires("_remote_client_factory", IRemoteClientFactory.__name__)
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
        self._remote_client_factory = None
        self._active = False
        self._remote_client_id = None
        self._remote_server = None

    def method_call(self, *args, **kwds):
        """return tuple of 2 element that admit a dictionnary of header and a body"""
        if self.test_service():
            w_kwds = kwds.copy()
            i = 0
            for arg in args:
                w_kwds["arg_{}".format(i)] = arg
                i = i + 1
            try:
                return self._client.method_call(**w_kwds)
            except:
                self.connect()
                return self._client.method_call(**w_kwds)

        return None

    def get_name(self):
        return self._name

    def get_remote_client_id(self):
        return self._remote_client_id

    def is_active(self):
        return self._active

    def test_service(self):
        try:
            # self._client.test()
            self._active = True
        except:
            self._active = False
            self._remote_client_factory.remove_remote_client(
                self.get_remote_client_id()
            )
        return self._active

    def ask_services(self):
        if self.test_service():
            remove_service = self._client.ask_services()
            self._remote_server.manage_services(remove_service)

    def update_services(self, remove_service):
        if self.test_service():
            self._client.update_services(remove_service)

    def validate_ask_service(self):
        self._client = connect(self._scheme, self._host, self._port)
        self.test_service()
        self.ask_services()

    def connect(self):
        self._client = connect(self._scheme, self._host, self._port)

    @Validate
    def validate(self, context):
        self._log.info("RemoteClient validating")
        self._context = context
        _threadExecutor = executor_service.new_executor("ValidateAskService")
        _callable = ValidateAskService(self)
        _threadExecutor.submit(_callable)
        self._log.info("RemoteClient validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteClient invalidating")

        self._log.info("RemoteClient invalidated")
