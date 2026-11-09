import threading
from datetime import datetime

from .web import HttpError, call, find, log

LOCATIONS = ('FGIN/GR/0001/0000/0000', 'FGIN/ST/0001/0000/0000')
NEXT_STATE = {'CREATED': 'STARTED', 'STARTED': 'FINISHED'}
RECEIVE = 'FGIN/GR/0001/0000/0000'
STORE = 'FGIN/ST/0001/0000/0000'
PALLET = '00000000000000004711'
VEHICLE = 'Vehicle-01'


class Wms:
    """独立 WMS。库位是自己的主数据，直接调用 SAP，并向车队派上架。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.units = {}
        self.orders = {}
        self.journal = []
        self.links = {}
        self.taken = set()
        self.stopped = threading.Event()
        self.sap = None
        self.fleet = None
        self.poll_seconds = 1.0

    def routes(self):
        return [
            ('POST', r'/transport-units', self.create_unit),
            ('GET', r'/transport-units/([^/]+)', self.get_unit),
            ('GET', r'/transport-orders', self.list_orders),
            ('POST', r'/transport-orders', self.create_order),
            ('GET', r'/transport-orders/([^/]+)', self.get_order),
            ('POST', r'/transport-orders/([^/]+)/state', self.change_state),
            ('GET', r'/status', self.status),
        ]

    def status(self, request):
        with self.lock:
            return {
                'links': {key: dict(value) for key, value in self.links.items()},
                'journal': list(self.journal[-50:]),
            }

    def start(self, sap_url, fleet_url, poll_seconds=1.0):
        self.sap, self.fleet, self.poll_seconds = sap_url, fleet_url, poll_seconds
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stopped.set()

    def create_unit(self, request):
        barcode, location = request.body['barcode'], request.body['location']
        with self.lock:
            unit = self._register(barcode, location, request.body.get('type', 'PALLET'))
        log('WMS', f'托盘 {barcode} 登记在 {location}')
        return 201, unit

    def get_unit(self, request, barcode):
        with self.lock:
            return dict(find(self.units, barcode, '托盘'))

    def list_orders(self, request):
        with self.lock:
            return [dict(order) for order in self.orders.values()]

    def get_order(self, request, order_id):
        with self.lock:
            return dict(find(self.orders, order_id, '运输单'))

    def create_order(self, request):
        barcode, target = request.body['barcode'], request.body['targetLocation']
        with self.lock:
            order = self._open_order(barcode, target)
        log('WMS', f'运输单 {order["id"]} 已创建：托盘 {barcode}，{order["sourceLocation"]} -> {target}')
        return 201, order

    def change_state(self, request, order_id):
        state = request.body['state']
        with self.lock:
            before, order = self._move_state(order_id, state)
        arrived = f'，托盘到达 {order["targetLocation"]}' if state == 'FINISHED' else ''
        log('WMS', f'运输单 {order_id}：{before} -> {state}{arrived}')
        return order

    def _register(self, barcode, location, kind='PALLET'):
        if location not in LOCATIONS:
            raise HttpError(400, f'没有库位 {location}')
        if barcode in self.units:
            raise HttpError(409, f'托盘 {barcode} 已经登记')
        self.units[barcode] = {'barcode': barcode, 'type': kind, 'location': location}
        return dict(self.units[barcode])

    def _open_order(self, barcode, target):
        unit = find(self.units, barcode, '托盘')
        if target not in LOCATIONS:
            raise HttpError(400, f'没有库位 {target}')
        order_id = f'TO-{len(self.orders) + 1:04d}'
        order = {'id': order_id, 'barcode': barcode, 'sourceLocation': unit['location'],
                 'targetLocation': target, 'state': 'CREATED'}
        self.orders[order_id] = order
        return dict(order)

    def _move_state(self, order_id, state):
        order = find(self.orders, order_id, '运输单')
        before = order['state']
        if NEXT_STATE.get(before) != state:
            raise HttpError(409, f'运输单 {order_id} 当前是 {before}，不能改成 {state}')
        order['state'] = state
        if state == 'FINISHED':
            self.units[order['barcode']]['location'] = order['targetLocation']
        return before, dict(order)

    def _loop(self):
        synced = False
        while not self.stopped.is_set():
            try:
                if not synced:
                    self._sync_master_data()
                    synced = True
                opened, _ = call('GET', f'{self.sap}/inbound-deliveries?goodsMovementStatus=A')
                for delivery in opened:
                    if delivery['delivery'] not in self.taken:
                        self.taken.add(delivery['delivery'])
                        self._receive(delivery)
            except Exception as error:
                self._note(f'这一轮出错：{error}')
            self.stopped.wait(self.poll_seconds)

    def _sync_master_data(self):
        products, _ = call('GET', f'{self.sap}/products')
        locations, _ = call('GET', f'{self.sap}/storage-locations')
        self._note('已从 SAP 同步物料主数据和库存地点：'
                   + '，'.join(f"{item['product']}/{item['baseUnit']}" for item in products)
                   + '；' + '，'.join(f"{item['plant']}/{item['storageLocation']}" for item in locations))
        self._note('开始轮询 SAP 未过账的入库交货')

    def _receive(self, delivery):
        number = delivery['delivery']
        site = f'{delivery["plant"]}/{delivery["storageLocation"]}'
        if site != '1000/FG00':
            self._note(f'入库交货 {number} 的工厂/库存地点 {site} 没有库位，SAP 保持未过账')
            return
        self._note(f'接手入库交货 {number}：{delivery["material"]} × {delivery["qty"]}，{site}')
        with self.lock:
            self._register(PALLET, RECEIVE)
            order = self._open_order(PALLET, STORE)
            self._move_state(order['id'], 'STARTED')
            self.links[number] = {'wms': order['id'], 'fleet': f'INB-{number}', 'material': delivery['material']}
        self._note(f'托盘登记在收货位，运输单 {order["id"]} 前往存储位')
        fleet_order = f'INB-{number}'
        call('POST', f'{self.fleet}/transport-orders/{fleet_order}', {
            'intendedVehicle': VEHICLE,
            'destinations': [
                {'locationName': 'GoodsReceipt', 'operation': 'Load cargo'},
                {'locationName': 'Storage-01', 'operation': 'Unload cargo'},
            ],
        })
        self._note(f'已向车队下发上架订单 {fleet_order}')
        while call('GET', f'{self.fleet}/transport-orders/{fleet_order}')[0]['state'] != 'FINISHED':
            if self.stopped.wait(self.poll_seconds):
                return
        with self.lock:
            self._move_state(order['id'], 'FINISHED')
        self._post_goods_receipt(number, delivery['qty'])
        self._note(f'入库交货 {number} 上架完成，收货已回写 SAP')

    def _post_goods_receipt(self, delivery, qty):
        body, headers = call('GET', f'{self.sap}/inbound-deliveries/{delivery}')
        posted, _ = call('POST', f'{self.sap}/inbound-deliveries/{delivery}/goods-receipt',
                         {'actualQty': body.get('qty', qty)}, {'If-Match': headers['ETag']})
        with self.lock:
            self.links[delivery]['materialDocument'] = posted['materialDocument']

    def _note(self, message):
        entry = {'at': datetime.now().isoformat(timespec='milliseconds'), 'message': message}
        with self.lock:
            self.journal.append(entry)
            del self.journal[:-200]
        log('WMS', message)
