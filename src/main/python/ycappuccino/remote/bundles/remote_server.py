"""
component that is a server remote that allow to call any ycapppuccino component
"""

import json
import time

from ycappuccino.api.proxy.api import YCappuccinoRemote
from ycappuccino.core.framework import Framework
from ycappuccino.core import executor_service
from ycappuccino.api.core.api import IActivityLogger, IConfiguration
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
from jsonrpclib.SimpleJSONRPCServer import SimpleJSONRPCServer
from ycappuccino.core.executor_service import Callable
from jsonrpclib.threadpool import ThreadPool
from ycappuccino.core.decorator_app import Layer
from ycappuccino.api.service_comm.api import (
    IRemoteClientFactory,
    IRemoteServer,
    IRemoteClient,
)
from ycappuccino.core.models import decorators
from ycappuccino.remote.bundles.remote_component_proxy import RemoteComponentProxy
from ycappuccino.api.service_comm.api import IRemoteManager
from ycappuccino.remote.models.remote_server import RemoteServer
from ycappuccino.api.storage.api import IItemManager

_logger = logging.getLogger(__name__)

service = None


def method_call(**kwds):
    """return tuple of 2 element that admit a dictionnary of header and a body"""
    global service
    res = service.method_call(**kwds)
    return res


def ask_services():
    """ " return list of services"""
    global service
    return service.ask_services()


def update_services(list_services):
    """ " return list of service"""
    global service
    return service.update_services(list_services)


def test():
    """ " return nothin"""
    pass


class ThreadReadRemote(Callable):
    def __init__(self, a_service, a_log):
        super(ThreadReadRemote, self).__init__("ThreadReadRemote")
        self._service = a_service
        self._log = a_log
        # Setup the thread pool: between 0 and 10 threads

    def run(self):
        time.sleep(1)
        self._service.create_remote_clients()


class ThreadRemoteServer(Callable):

    def __init__(self, a_service, a_log):
        super(ThreadRemoteServer, self).__init__("ThreadRemoteServer")
        self._service = a_service
        self._pool = ThreadPool(max_threads=10, min_threads=0)
        self._server = None
        self._log = a_log

    # Setup the thread pool: between 0 and 10 threads

    def get_server_port(self):
        while self._server is None:
            pass
        return (
            self._server.server_address[1]
            if self._server is not None
            and "server_address" in self._server.__dict__.keys()
            else None
        )

    def run(self):
        if self._log is not None:
            self._log.info(
                "IRemoteServer listen host={}, port={}".format(
                    self._service.get_host(), self._service.get_config_port()
                )
            )
        self._server = SimpleJSONRPCServer(
            (self._service.get_host(), self._service.get_config_port())
        )
        self._server.set_notification_pool(self._pool)
        self._server.register_function(ask_services)
        self._server.register_function(update_services)
        self._server.register_function(method_call)
        self._server.register_function(test)
        print("remote {} listening".format(self._service.get_remote_server_id()))
        self._server.serve_forever()
        try:
            self._server.serve_forever()
        finally:
            # Stop the thread pool (let threads finish their current task)
            self._pool.stop()
            self._server.set_notification_pool(None)


@ComponentFactory("RemoteServerService-Factory")
@Provides(specifications=[IRemoteServer.__name__])
@Requires("_log", IActivityLogger.__name__, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.__name__)
@Requires(
    "_remote_components",
    specification=YCappuccinoRemote.__name__,
    optional=True,
    aggregate=True,
)
@Requires("_remote_client_factory", IRemoteClientFactory.__name__)
@Requires("_remote_clients", IRemoteClient.__name__, optional=True, aggregate=True)
@Requires("_manager_remote_server", IRemoteManager.__name__)
@Requires("_item_manager", IItemManager.__name__, optional=True, aggregate=True)
@Instantiate("RemoteServerService")
@Layer(name="ycappuccino_service_comm_storage")
class RemoteServerService(IRemoteServer):

    def __init__(self):
        super().__init__()
        global service
        service = self
        self.framework = Framework.get_framework()
        self._context = None
        self._remote_components = None
        self._map_remote_components = {}
        self._map_proxy_components = {}
        self._map_servce_registration = {}
        self._callableRead = None
        self._callable = None
        self._map_add_remote_components = {}
        self._map_del_remote_components = {}
        self._remote_clients = None
        self._map_remote_clients = {}
        self._threadExecutor = None
        self._threadExecutorRead = None
        self._manager_remote_server = None
        self._remote_client_factory = None
        prop_layer = self.framework.get_layer_properties("ycappuccino_service_comm")
        self._host = prop_layer["host"] if "host" in prop_layer else "localhost"
        self._scheme = prop_layer["scheme"] if "scheme" in prop_layer else "http"
        self._port = prop_layer["port"] if "port" in prop_layer else 8080
        self._log = None
        self._item_manager_proxy = None

    def manage_services(self, a_services):
        if a_services is not None:
            if "add" in a_services:
                self.manage_add_services(a_services["add"])
            if "del" in a_services:
                self.manage_del_services(a_services["del"])

    def ask_services(self):
        # run create remote to create missing client
        self.create_remote_clients()

        # analyse move regarding service to delete some of them and create other
        res = self.get_events_services()
        self.raz_update_remote_component()
        return res

    def send_services(self):
        # run create remote to create missing client
        if self._remote_clients is not None and len(self._remote_clients) > 0:
            for remote_client in self._remote_clients:
                remote_client.update_services(self.get_events_services())
                self.raz_update_remote_component()

    def update_services(self, w_services_remote):
        # run create remote to create missing client
        self.manage_add_services(w_services_remote["add"])
        self.manage_del_services(w_services_remote["del"])

    def get_events_services(self):
        w_services_local = {
            "add": self._map_add_remote_components.copy(),
            "del": self._map_del_remote_components.copy(),
        }
        print("get_events_services {}".format(json.dumps(w_services_local)))
        return w_services_local

    def raz_update_remote_component(self):

        self._map_add_remote_components.clear()
        self._map_del_remote_components.clear()
        print("raz_update_remote_component ")

    def manage_add_services(self, a_add_services):
        for interface in a_add_services:
            for prop_id in a_add_services[interface]:
                properties = json.loads(prop_id)
                remote_client_id = properties["remote_server_id"]
                specifications = properties["specifications"]
                methods = properties["methods"]

                # remove YCappuccinoRemote spec
                spec_to_register = []
                for spec in specifications:
                    if spec != YCappuccinoRemote.name:
                        spec_to_register.append(spec)
                remote_client = self.get_remote_client(remote_client_id)
                w_remote_component_proxy = RemoteComponentProxy(
                    self._log, remote_client, spec_to_register, properties, methods
                )
                if remote_client_id not in self._map_servce_registration:
                    self._map_servce_registration[remote_client_id] = {}
                print("register new service {}".format(spec_to_register))
                if "ycappuccino_api.IItemManager" in spec_to_register:
                    # add item from this instance of app
                    self._item_manager_proxy = w_remote_component_proxy
                    for item in decorators.get_map_items():
                        w_item = item.copy()
                        del w_item["_class_obj"]
                        self._item_manager_proxy.load_item(w_item)
                self._map_servce_registration[remote_client_id][prop_id] = (
                    self._context.register_service(
                        spec_to_register,
                        w_remote_component_proxy,
                        properties,
                        factory=False,
                    )
                )

    def manage_del_services(self, a_add_services):
        for interface in a_add_services:
            for prop_id in a_add_services[interface]:
                properties = json.loads(prop_id)
                remote_client_id = properties["remote_server_id"]

                if remote_client_id in self._map_servce_registration:
                    if prop_id in self._map_servce_registration[remote_client_id]:
                        self._context.unregister_service(
                            self._map_servce_registration[remote_client_id][prop_id]
                        )

    def get_attribute(self, a_specs, a_properties_id, a_name):
        """return tuple of 2 element that admit a dictionnary of header and a body"""
        for interface in a_specs:
            if interface != YCappuccinoRemote.name:
                if interface in self._map_remote_components.keys():
                    if a_properties_id in self._map_remote_components[interface].keys():
                        return self._map_remote_components[interface][
                            a_properties_id
                        ].__getattribute__(a_name)

        return None

    def method_call(self, **kwds):
        """return tuple of 2 element that admit a dictionnary of header and a body"""
        w_kwds = kwds.copy()
        w_args = []
        for n in range(len(w_kwds)):
            if "arg_{}".format(n) in w_kwds.keys():
                w_args.append(w_kwds["arg_{}".format(n)])
                del w_kwds["arg_{}".format(n)]
            else:
                break

        specs = kwds["specifications"]
        properties_id = kwds["properties_id"]
        name = kwds["name"]

        del w_kwds["specifications"]
        del w_kwds["properties_id"]
        del w_kwds["name"]

        for interface in specs:
            if interface != YCappuccinoRemote.name:
                if interface in self._map_remote_components.keys():
                    if properties_id in self._map_remote_components[interface].keys():
                        return getattr(
                            self._map_remote_components[interface][properties_id], name
                        )(*w_args, **w_kwds)

        return None

    @BindField("_remote_components")
    def bind_remote_component(self, field, a_service, a_service_reference):
        if self._manager_remote_server is not None:
            self.create_thread()

            for interface in a_service_reference.get_properties()["objectClass"]:
                if interface != YCappuccinoRemote.name:
                    if interface not in self._map_remote_components:
                        self._map_remote_components[interface] = {}
                    if interface not in self._map_add_remote_components:
                        self._map_add_remote_components[interface] = []
                    a_service.set_component_properties(
                        a_service_reference._ServiceReference__properties
                    )
                    a_service.set_component_id_remote(self.get_remote_server_id())

                    w_id_property = a_service.get_component_properties_id()
                    if w_id_property not in self._map_remote_components[interface]:
                        self._map_remote_components[interface][
                            w_id_property
                        ] = a_service
                    self._map_add_remote_components[interface].append(w_id_property)
                    self.send_services()

    @BindField("_remote_clients")
    def bind_remote_client(self, field, a_service, a_service_reference):
        self._map_remote_clients[a_service.get_remote_client_id()] = a_service

    def get_remote_client(self, a_id):
        return self._map_remote_clients[a_id]

    def is_remote_client(self, a_id):
        return a_id in self._map_remote_clients

    @UnbindField("_remote_clients")
    def un_bind_remote_client(self, field, a_service, a_service_reference):
        del self._map_remote_clients[a_service.get_remote_client_id()]

    def get_host(self):
        return self._host

    def get_port(self):
        return self._callable.get_server_port()

    def get_config_port(self):
        return self._port

    @UnbindField("_remote_components")
    def unbind_components(self, field, a_service, a_service_reference):
        for interface in a_service_reference.get_properties()["objectClass"]:
            if interface not in self._map_remote_components:
                self._map_remote_components[interface] = {}
            if interface not in self._map_add_remote_components:
                self._map_del_remote_components[interface] = []
            w_id_property = a_service.get_component_properties_id()
            if w_id_property not in self._map_remote_components[interface]:
                del self._map_remote_components[interface][w_id_property]
            self._map_del_remote_components[interface].append(w_id_property)

    def get_remote_server_id(self):
        return "{}_{}_{}_{}".format(
            self._scheme, self._host, self.framework.get_app_name(), self.get_port()
        )

    def create_remote_clients(self):
        w_subject = self.get_token_subject("bootstrap", "system")
        w_offset = 0
        none = False
        w_remote_local_id = self.get_remote_server_id()
        while not none:
            list_remote_server = self._manager_remote_server.get_many(
                "remoteServer", {"offset": w_offset, "size": 50}, w_subject
            )
            none = len(list_remote_server) <= 0
            for remote_server in list_remote_server:
                if remote_server._id != w_remote_local_id:
                    if not self.is_remote_client(remote_server._id):
                        ok = self._remote_client_factory.create_remote_client(
                            remote_server
                        )
                        if not ok:
                            self._manager_remote_server.delete(
                                "remoteServer", remote_server._id, w_subject
                            )
                    else:
                        self._manager_remote_server.delete(
                            "remoteServer", remote_server._id, w_subject
                        )

            w_offset = w_offset + 50
        # delete all remote client that doesn't work

    def check_and_create_remote_server(self):
        if self._manager_remote_server is not None:
            w_server = RemoteServer()
            w_server.id(self.get_remote_server_id())
            w_server.scheme(self._scheme)
            w_server.host(self._host)
            w_server.port(self._callable.get_server_port())
            w_subject = self.get_token_subject("bootstrap", "system")

            self._manager_remote_server.up_sert_model(w_server._id, w_server, w_subject)

    def create_thread(self):
        if self._threadExecutor is None:
            self._threadExecutor = executor_service.new_executor("ThreadRemoteServer")
            self._callable = ThreadRemoteServer(self, self._log)
            self._threadExecutor.submit(self._callable)

        if self._threadExecutorRead is None:
            self._threadExecutorRead = executor_service.new_executor("ThreadReadRemote")
            self._callableRead = ThreadReadRemote(self, self._log)
            self._threadExecutorRead.submit(self._callableRead)

    @Validate
    def validate(self, context):
        self._log.info("RemoteServer validating")
        self._context = context
        self.create_thread()
        self.check_and_create_remote_server()

        self._log.info("RemoteServer validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteServer invalidating")
        self._context = None

        if self._threadExecutor is not None:
            self._threadExecutor.shutdown()
        self._log.info("RemoteServer invalidated")
