import copy
import threading
from collections import deque

from .web import HttpError, call, find, log

PATHS = [
    ('Point-Park', 'Point-GR'), ('Point-GR', 'Point-Park'),
    ('Point-GR', 'Point-Store'), ('Point-Store', 'Point-GR'),
]
LOCATIONS = {'GoodsReceipt': 'Point-GR', 'Storage-01': 'Point-Store'}


class Fleet:
    def __init__(self, vehicles=('Vehicle-01',)):
        self.lock = threading.Lock()
        self.vehicles = {
            name: {'name': name, 'state': 'UNAVAILABLE', 'position': None, 'transportOrder': None}
            for name in vehicles
        }
        self.vehicle_urls = {}
        self.orders = {}

    def routes(self):
        return [
            ('GET', r'/vehicles', self.list_vehicles),
            ('POST', r'/vehicles/([^/]+)/reports', self.report),
            ('GET', r'/transport-orders', self.list_orders),
            ('GET', r'/transport-orders/([^/]+)', self.get_order),
            ('POST', r'/transport-orders/([^/]+)', self.create_order),
        ]

    def list_vehicles(self, request):
        with self.lock:
            return copy.deepcopy(list(self.vehicles.values()))

    def list_orders(self, request):
        with self.lock:
            return copy.deepcopy(list(self.orders.values()))

    def get_order(self, request, name):
        with self.lock:
            return copy.deepcopy(find(self.orders, name, '运输订单'))

    def create_order(self, request, name):
        destinations = request.body['destinations']
        with self.lock:
            if name in self.orders:
                raise HttpError(409, f'运输订单 {name} 已存在')
            for destination in destinations:
                if destination['locationName'] not in LOCATIONS:
                    raise HttpError(400, f'没有位置 {destination["locationName"]}')
            self.orders[name] = {
                'name': name, 'state': 'DISPATCHABLE',
                'intendedVehicle': request.body.get('intendedVehicle'), 'vehicle': None,
                'destinations': [{**destination, 'state': 'PRISTINE'} for destination in destinations],
            }
            steps = '，'.join(f'{item["locationName"]} {item["operation"]}' for item in destinations)
            log('车队', f'运输订单 {name} 等待分配：{steps}')
            sends = self._dispatch()
            order = copy.deepcopy(self.orders[name])
        self._send(sends)
        return 201, order

    def report(self, request, name):
        event, position = request.body['event'], request.body['position']
        with self.lock:
            vehicle = find(self.vehicles, name, '车辆')
            vehicle['position'] = position
            sends = []
            if event == 'online':
                self.vehicle_urls[name] = request.body['url']
                vehicle['state'] = 'IDLE'
                log('车队', f'{name} 上线，位置 {position}')
            elif event == 'done':
                sends += self._advance(self.orders[vehicle['transportOrder']], vehicle)
            sends += self._dispatch()
        self._send(sends)
        return {'ok': True}

    def _dispatch(self):
        sends = []
        for order in self.orders.values():
            if order['state'] != 'DISPATCHABLE':
                continue
            idle = [vehicle for vehicle in self.vehicles.values()
                    if vehicle['state'] == 'IDLE' and order['intendedVehicle'] in (None, vehicle['name'])]
            if not idle:
                continue
            vehicle = idle[0]
            order.update(state='BEING_PROCESSED', vehicle=vehicle['name'])
            vehicle.update(state='EXECUTING', transportOrder=order['name'])
            log('车队', f'运输订单 {order["name"]}：DISPATCHABLE -> BEING_PROCESSED，分配给 {vehicle["name"]}')
            sends.append(self._next_drive(order, vehicle))
        return sends

    def _advance(self, order, vehicle):
        current = next(item for item in order['destinations'] if item['state'] == 'TRAVELLING')
        current['state'] = 'FINISHED'
        if any(item['state'] == 'PRISTINE' for item in order['destinations']):
            return [self._next_drive(order, vehicle)]
        order['state'] = 'FINISHED'
        vehicle.update(state='IDLE', transportOrder=None)
        log('车队', f'运输订单 {order["name"]}：BEING_PROCESSED -> FINISHED')
        return []

    def _next_drive(self, order, vehicle):
        index, destination = next((number, item) for number, item in enumerate(order['destinations'], 1)
                                  if item['state'] == 'PRISTINE')
        destination['state'] = 'TRAVELLING'
        route = _route(vehicle['position'], LOCATIONS[destination['locationName']])
        path = ' -> '.join([vehicle['position'], *route])
        log('车队', f'下发第 {index} 段给 {vehicle["name"]}：{path}，到达后 {destination["operation"]}')
        return self.vehicle_urls[vehicle['name']], {
            'transportOrder': order['name'], 'route': route, 'operation': destination['operation'],
        }

    def _send(self, sends):
        for url, drive_order in sends:
            call('POST', url + '/drive-orders', drive_order)


def _route(start, goal):
    previous = {start: None}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        for source, target in PATHS:
            if source == point and target not in previous:
                previous[target] = point
                queue.append(target)
    steps = []
    while goal != start:
        steps.append(goal)
        goal = previous[goal]
    return steps[::-1]
