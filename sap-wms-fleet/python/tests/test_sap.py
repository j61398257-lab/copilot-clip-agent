import unittest

from chain import sap, web


class SapRulesTest(unittest.TestCase):
    def setUp(self):
        self.server = web.serve(sap.Sap().routes())
        self.addCleanup(self.server.stop)
        self.delivery = self.server.url + '/inbound-deliveries/1800000001'

    def test_goods_receipt_with_stale_etag_is_rejected(self):
        with self.assertRaises(web.HttpError) as caught:
            web.call('POST', self.delivery + '/goods-receipt', {'actualQty': 1}, {'If-Match': 'W/"0"'})
        self.assertEqual(caught.exception.status, 412)

    def test_second_goods_receipt_is_rejected(self):
        _, headers = web.call('GET', self.delivery)
        posted, posted_headers = web.call('POST', self.delivery + '/goods-receipt', {'actualQty': 1},
                                          {'If-Match': headers['ETag']})
        self.assertEqual((posted['goodsMovementStatus'], posted['materialDocument']), ('C', '5000000001'))
        with self.assertRaises(web.HttpError) as caught:
            web.call('POST', self.delivery + '/goods-receipt', {'actualQty': 1},
                     {'If-Match': posted_headers['ETag']})
        self.assertEqual(caught.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
