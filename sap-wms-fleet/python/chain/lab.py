from . import web
from .fleet import Fleet
from .sap import Sap
from .vehicle import Vehicle
from .wms import Wms

PORTS = {'sap': 9004, 'wms': 9120, 'fleet': 9200, 'vehicle': 9300}


class Lab:
    def __init__(self, servers, wms):
        self.servers = servers
        self.wms = wms
        self.urls = {name: server.url for name, server in servers.items()}

    def stop(self):
        self.wms.stop()
        for server in self.servers.values():
            server.stop()


def start(ports=PORTS, host='127.0.0.1', step_seconds=1.0, poll_seconds=1.0):
    vehicle = Vehicle(step_seconds=step_seconds)
    wms = Wms()
    services = {'sap': Sap(), 'wms': wms, 'fleet': Fleet(), 'vehicle': vehicle}
    servers = {name: web.serve(service.routes(), ports[name], host) for name, service in services.items()}
    vehicle.connect(servers['fleet'].url, servers['vehicle'].url)
    wms.start(servers['sap'].url, servers['fleet'].url, poll_seconds)
    return Lab(servers, wms)
