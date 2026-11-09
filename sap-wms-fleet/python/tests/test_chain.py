import time
import unittest

from chain import lab, web


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.lab = lab.start(ports={name: 0 for name in lab.PORTS}, step_seconds=0.02, poll_seconds=0.02)
        self.addCleanup(self.lab.stop)

    def get(self, system, path):
        body, _ = web.call('GET', self.lab.urls[system] + path)
        return body

    def wait_until_posted(self, delivery, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            document = self.get('sap', f'/inbound-deliveries/{delivery}')
            if document['goodsMovementStatus'] == 'C':
                return document
            if time.monotonic() > deadline:
                self.fail(f'入库交货 {delivery} 在 {timeout} 秒内没有过账，停在 {document["goodsMovementStatus"]}')
            time.sleep(0.05)

    def test_inbound_delivery_is_put_away_and_goods_receipt_posted(self):
        delivery = self.wait_until_posted('1800000001')
        documents = self.get('sap', '/material-documents')
        orders = self.get('wms', '/transport-orders')
        pallet = self.get('wms', '/transport-units/00000000000000004711')
        fleet_order = self.get('fleet', '/transport-orders/INB-1800000001')
        vehicle = next(item for item in self.get('fleet', '/vehicles') if item['name'] == 'Vehicle-01')
        outbound = self.get('sap', '/outbound-deliveries/8000000001')
        self.assertEqual(delivery['materialDocument'], '5000000001')
        self.assertEqual(documents[0]['movementType'], '101')
        self.assertEqual(
            (orders[0]['state'], orders[0]['sourceLocation'], orders[0]['targetLocation'], pallet['location']),
            ('FINISHED', 'FGIN/GR/0001/0000/0000', 'FGIN/ST/0001/0000/0000', 'FGIN/ST/0001/0000/0000'))
        self.assertEqual((fleet_order['state'], vehicle['position']), ('FINISHED', 'Point-Store'))
        self.assertEqual(outbound['goodsMovementStatus'], 'A')


if __name__ == '__main__':
    unittest.main()
