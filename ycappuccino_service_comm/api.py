#app="all"

from ycappuccino_core.api import CFQCN, YCappuccino
from ycappuccino_core.models.utils import Proxy
from ycappuccino_storage.api import IRightSubject



class YCappuccinoProxy(YCappuccino, Proxy):
    """ interface of YCappuccino component """
    name = CFQCN.build("YCappuccinoProxy")

    def __init__(self):
        """ abstract constructor """
        super().__init__()


class IRemoteServer(IRightSubject):
    """ interface of proxy component that allow to bind all
    YCappuccino ycappuccino_core component and notify client ipopo of ycappuccino_core component"""
    name = CFQCN.build("IRemoteServer")

    def __init__(self):
        """ abstract constructor """
        super().__init__()


class IRemoteComponentProxy(object):
    """ interface of YCappuccino component """
    name = CFQCN.build("IRemoteComponentProxy")

    def __init__(self):
        """ abstract constructor """
        pass

class IRemoteClient(object):
    """ interface of proxy component that allow to bind all
    YCappuccino ycappuccino_core component and notify client ipopo of ycappuccino_core component"""
    name = CFQCN.build("IRemoteClient")

    def __init__(self):
        """ abstract constructor """
        pass

class IRemoteClientFactory(object):
    """ interface of proxy component that allow to bind all
    YCappuccino ycappuccino_core component and notify client ipopo of ycappuccino_core component"""
    name = CFQCN.build("IRemoteClientFactory")

    def __init__(self):
        """ abstract constructor """
        pass

    def create_remote_client(self, a_remote_server):
        pass

    def remove_remote_client(self, a_remote_server):
        pass
