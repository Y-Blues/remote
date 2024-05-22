# app="all"
from pelix.ipopo.constants import use_ipopo

from ycappuccino.api.core import IActivityLogger, IConfiguration
import logging
from pelix.ipopo.decorators import (
    ComponentFactory,
    Requires,
    Validate,
    Invalidate,
    Provides,
    Instantiate,
)

import ycappuccino.core
from ycappuccino.core.decorator_app import Layer
from ycappuccino.api.remote import IRemoteStorageFactory

_logger = logging.getLogger(__name__)


@ComponentFactory("RemoteStorageFactory-Factory")
@Provides(specifications=[IRemoteStorageFactory.__name__], controller="_available")
@Requires("_log", IActivityLogger.__name__, spec_filter="'(name=main)'")
@Requires("_config", IConfiguration.__name__)
@Instantiate("RemoteStorageFactory")
@Layer(name="ycappuccino-remote_storage")
class RemoteStorageFactory(IRemoteStorageFactory):

    def __init__(self):
        super().__init__()
        self._log = None

    @Validate
    def validate(self, context):
        self._log.info("RemoteStorageFactory validating")
        prop_layer = (
            ycappuccino_core.framework.Framework.get_framework().get_layer_properties(
                "ycappuccino-remote_storage"
            )
        )
        if "type" in prop_layer.keys():
            type = prop_layer["type"]
            if type == "mongo":
                with use_ipopo(context) as ipopo:
                    ipopo.instantiate(
                        "MongoRemoteStorage-Factory", "MongoRemoteStorage", {}
                    )
        self._log.info("RemoteStorageFactory validated")

    @Invalidate
    def invalidate(self, context):
        self._log.info("RemoteStorageFactory invalidating")

        self._log.info("RemoteStorageFactory invalidated")
