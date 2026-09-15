"""
Demo IExposedService, called remotely by example/'s RemoteCall through this peer's
http_server. See remote/README.md.
"""

from ycappuccino.api.endpoints_service import IExposedService, ServiceResult


class Greeting(IExposedService):
    name = "greeting"
    secure = False

    def __init__(self):
        pass

    async def call(self, method, extra_path, params, body, subject):
        return ServiceResult(body={"greeting": f"hello {body['name']}"})

    async def start(self):
        pass

    async def stop(self):
        pass
