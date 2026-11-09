const test = require('node:test')
const assert = require('node:assert/strict')
const { createServer, reset, PRODUCT, INBOUND, OUTBOUND, MATERIAL } = require('./server')

let base
let server

test.before(async () => {
  server = createServer()
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${server.address().port}`
})

test.after(() => new Promise((resolve) => server.close(resolve)))

test.beforeEach(() => reset())

async function csrf() {
  const response = await fetch(`${base}${INBOUND}/`, { method: 'HEAD', headers: { 'X-CSRF-Token': 'Fetch' } })
  return response.headers.get('x-csrf-token')
}

test('open inbound delivery, master data and pending outbound are readable', async () => {
  const inbound = await fetch(`${base}${INBOUND}/A_InbDeliveryHeader?$filter=${encodeURIComponent("OverallGoodsMovementStatus eq 'A'")}`)
  const body = await inbound.json()
  assert.equal(body.d.results[0].DeliveryDocument, '1800000001')
  assert.equal(body.d.results[0].to_DeliveryDocumentItem.results[0].Material, 'MAT-100')
  assert.equal(body.d.results[0].to_DeliveryDocumentItem.results[0].Plant, '1000')
  const product = await (await fetch(`${base}${PRODUCT}/A_Product('MAT-100')`)).json()
  assert.equal(product.d.BaseUnit, 'EA')
  const sloc = await (await fetch(`${base}${PRODUCT}/A_ProductStorageLocation`)).json()
  assert.deepEqual(
    sloc.d.results.map((row) => `${row.Plant}/${row.StorageLocation}`),
    ['1000/FG00'],
  )
  const outbound = await (await fetch(`${base}${OUTBOUND}/A_OutbDeliveryHeader('8000000001')`)).json()
  assert.equal(outbound.d.OverallGoodsMovementStatus, 'A')
})

test('goods receipt posts a 101 material document and rejects a second post', async () => {
  const token = await csrf()
  const read = await fetch(`${base}${INBOUND}/A_InbDeliveryHeader('1800000001')`)
  const missing = await fetch(`${base}${INBOUND}/PostGoodsReceipt?DeliveryDocument='1800000001'`, {
    method: 'POST',
    headers: { 'If-Match': read.headers.get('etag') },
  })
  assert.equal(missing.status, 403)
  const stale = await fetch(`${base}${INBOUND}/PostGoodsReceipt?DeliveryDocument='1800000001'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token, 'If-Match': 'W/"0"' },
  })
  assert.equal(stale.status, 412)
  const noMatch = await fetch(`${base}${INBOUND}/PostGoodsReceipt?DeliveryDocument='1800000001'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token },
  })
  assert.equal(noMatch.status, 428)
  const posted = await fetch(`${base}${INBOUND}/PostGoodsReceipt?DeliveryDocument='1800000001'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token, 'If-Match': read.headers.get('etag'), 'Content-Type': 'application/json' },
    body: '{}',
  })
  const delivery = await posted.json()
  assert.equal(posted.status, 200)
  assert.equal(delivery.d.OverallGoodsMovementStatus, 'C')
  assert.equal(delivery.d.MaterialDocument, '5000000001')
  const documents = await (await fetch(`${base}${MATERIAL}/A_MaterialDocumentItem?$filter=${encodeURIComponent("ReferenceDocument eq '1800000001'")}`)).json()
  assert.equal(documents.d.results[0].GoodsMovementType, '101')
  assert.equal(documents.d.results[0].Plant, '1000')
  assert.equal(documents.d.results[0].StorageLocation, 'FG00')
  const again = await fetch(`${base}${INBOUND}/PostGoodsReceipt?DeliveryDocument='1800000001'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token, 'If-Match': posted.headers.get('etag') },
  })
  assert.equal(again.status, 400)
})

test('creating an inbound delivery allocates the next number', async () => {
  const token = await csrf()
  const created = await fetch(`${base}${INBOUND}/A_InbDeliveryHeader`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      Supplier: 'V200',
      to_DeliveryDocumentItem: { results: [{ Material: 'MAT-100', ActualDeliveryQuantity: 2, PurchaseOrder: '4500000002', PurchaseOrderItem: '00010' }] },
    }),
  })
  const body = await created.json()
  assert.equal(created.status, 201)
  assert.equal(body.d.DeliveryDocument, '1800000002')
  assert.equal(body.d.to_DeliveryDocumentItem.results[0].ActualDeliveryQuantity, '2.000')
})

test('goods issue posts a 601 material document for the outbound delivery', async () => {
  const token = await csrf()
  const read = await fetch(`${base}${OUTBOUND}/A_OutbDeliveryHeader('8000000001')`)
  const posted = await fetch(`${base}${OUTBOUND}/PostGoodsIssue?DeliveryDocument='8000000001'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': token, 'If-Match': read.headers.get('etag') },
  })
  const body = await posted.json()
  assert.equal(body.d.OverallGoodsMovementStatus, 'C')
  const documents = await (await fetch(`${base}${MATERIAL}/A_MaterialDocumentHeader?$filter=${encodeURIComponent("ReferenceDocument eq '8000000001'")}`)).json()
  assert.equal(documents.d.results[0].GoodsMovementCode, '03')
  assert.equal(documents.d.results[0].to_MaterialDocumentItem.results[0].GoodsMovementType, '601')
})
