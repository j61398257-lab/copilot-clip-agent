import threading

from .web import HttpError, find, log


class Sap:
    """简化过的 SAP ERP。完整版的同名对象在 4004，协议是 OData V2。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.products = [{'product': 'MAT-100', 'productType': 'HAWA', 'baseUnit': 'EA'}]
        self.storage_locations = [{'product': 'MAT-100', 'plant': '1000', 'storageLocation': 'FG00'}]
        self.inbound = {
            '1800000001': self._delivery('1800000001', 'EL', 'V100', '101', '4500000001'),
        }
        self.outbound = {
            '8000000001': self._delivery('8000000001', 'LF', 'C100', '601', ''),
        }
        self.material_documents = []
        self._next_material_document = 5000000001

    def routes(self):
        return [
            ('GET', r'/products', self.list_products),
            ('GET', r'/storage-locations', self.list_storage_locations),
            ('GET', r'/inbound-deliveries', self.list_inbound),
            ('GET', r'/inbound-deliveries/([^/]+)', self.get_inbound),
            ('POST', r'/inbound-deliveries/([^/]+)/goods-receipt', self.goods_receipt),
            ('GET', r'/outbound-deliveries', self.list_outbound),
            ('GET', r'/outbound-deliveries/([^/]+)', self.get_outbound),
            ('POST', r'/outbound-deliveries/([^/]+)/goods-issue', self.goods_issue),
            ('GET', r'/material-documents', self.list_documents),
        ]

    def list_products(self, request):
        return self.products

    def list_storage_locations(self, request):
        return self.storage_locations

    def list_inbound(self, request):
        return self._list(self.inbound, request.query.get('goodsMovementStatus'))

    def get_inbound(self, request, delivery):
        with self.lock:
            document = dict(find(self.inbound, delivery, '入库交货'))
        return 200, document, {'ETag': _etag(document)}

    def list_outbound(self, request):
        return self._list(self.outbound, request.query.get('goodsMovementStatus'))

    def get_outbound(self, request, delivery):
        with self.lock:
            document = dict(find(self.outbound, delivery, '出库交货'))
        return 200, document, {'ETag': _etag(document)}

    def goods_receipt(self, request, delivery):
        return self._post(request, self.inbound, delivery, '101', '入库交货')

    def goods_issue(self, request, delivery):
        return self._post(request, self.outbound, delivery, '601', '出库交货')

    def list_documents(self, request):
        with self.lock:
            return [dict(document) for document in self.material_documents]

    def _list(self, book, status):
        with self.lock:
            return [dict(document) for document in book.values()
                    if status in (None, document['goodsMovementStatus'])]

    def _post(self, request, book, delivery, movement, kind):
        with self.lock:
            document = find(book, delivery, kind)
            if request.headers.get('If-Match') != _etag(document):
                raise HttpError(412, f'{kind} {delivery} 已经被改过，先重新读取拿到新的 ETag')
            if document['goodsMovementStatus'] != 'A':
                raise HttpError(409, f'{kind} {delivery} 当前是 {document["goodsMovementStatus"]}，不能过账')
            number = f'{self._next_material_document:010d}'
            self._next_material_document += 1
            document.update(goodsMovementStatus='C', materialDocument=number, version=document['version'] + 1)
            posted = dict(document)
            self.material_documents.append({
                'materialDocument': number,
                'movementType': movement,
                'delivery': delivery,
                'material': document['material'],
                'qty': request.body.get('actualQty', document['qty']),
                'plant': document['plant'],
                'storageLocation': document['storageLocation'],
            })
        log('SAP', f'{kind} {delivery} 已过账，物料凭证 {number}，移动类型 {movement}')
        return 200, posted, {'ETag': _etag(posted)}

    def _delivery(self, delivery, document_type, partner, movement, purchase_order):
        return {
            'delivery': delivery,
            'type': document_type,
            'partner': partner,
            'material': 'MAT-100',
            'qty': 1,
            'uom': 'EA',
            'plant': '1000',
            'storageLocation': 'FG00',
            'purchaseOrder': purchase_order,
            'goodsMovementStatus': 'A',
            'movementType': movement,
            'materialDocument': None,
            'version': 1,
        }


def _etag(document):
    return f'W/"{document["version"]}"'
