from ycappuccino.api.remote import IRemoteStorage, IRemoteManager
from ycappuccino.remote.bundle.remote_server import RemoteServer
from ycappuccino.storage.bundles.managers import AbsManager
from ycappuccino.api.core import IActivityLogger
from pelix.ipopo.decorators import (
    ComponentFactory,
    Requires,
    Validate,
    Invalidate,
    Provides,
    Instantiate,
)

from ycappuccino.core.decorator_app import Layer


@ComponentFactory("IRemoteManager-Factory")
@Provides(specifications=[IRemoteManager.__name__])
@Requires("_log", IActivityLogger.__name__, spec_filter="'(name=main)'")
@Requires("_storage", IRemoteStorage.__name__)
@Instantiate("RemoteManager")
@Layer(name="ycappuccino-remote_storage")
class RemoteManager(AbsManager):

    def __init__(self):
        super(RemoteManager, self).__init__()

    def add_item(self, a_item, a_bundle_context):
        """add item in map manage by the manager"""
        super(RemoteManager, self).add_item(a_item, a_bundle_context)

    def remove_item(self, a_item, a_bundle_context):
        """add item in map manage by the manager"""
        super(RemoteManager, self).remove_item(a_item, a_bundle_context)

    def get_sons_item_id(self, a_item):
        """override sons item"""
        return ["remoteServer"]

    @Validate
    def validate(self, context):
        self._log.info("RemoteManager  validating")
        try:
            w_item = {
                "id": "remoteServer",
                "module": "system",
                "abstract": False,
                "collection": "remoteServers",
                "plural": "remoteServers",
                "secureRead": False,
                "secureWrite": True,
                "isWritable": True,
                "app": "remote",
                "multipart": False,
                "schema": {},
                "empty": None,
                "_class": RemoteServer.__name__,
                "_class_obj": RemoteServer,
            }
            self.add_item(w_item, context)
        except Exception as e:
            self._log.error("RemoteManager Error ".format(e))
            self._log.exception(e)

        self._log.info("RemoteManager  validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteManager  invalidating")

        self._log.info("RemoteManager  invalidated")
