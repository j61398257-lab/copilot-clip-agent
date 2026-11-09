const http = require('http')
const crypto = require('crypto')

const PORT = Number(process.env.PORT || 4004)
const token = crypto.randomBytes(18).toString('base64url')

const PRODUCT = '/sap/opu/odata/sap/API_PRODUCT_SRV'
const INBOUND = '/sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002'
const OUTBOUND = '/sap/opu/odata/sap/API_OUTBOUND_DELIVERY_SRV;v=0002'
const MATERIAL = '/sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV'

const SERVICES = {
  [PRODUCT]: ['A_Product', 'A_ProductPlant', 'A_ProductStorageLocation', 'A_ProductUnitsOfMeasure'],
  [INBOUND]: ['A_InbDeliveryHeader', 'A_InbDeliveryItem'],
  [OUTBOUND]: ['A_OutbDeliveryHeader', 'A_OutbDeliveryItem'],
  [MATERIAL]: ['A_MaterialDocumentHeader', 'A_MaterialDocumentItem'],
}

function seed() {
  return {
    products: [
      { Product: 'MAT-100', ProductType: 'HAWA', BaseUnit: 'EA', ProductGroup: 'L001' },
    ],
    plants: [
      { Product: 'MAT-100', Plant: '1000' },
    ],
    storageLocations: [
      { Product: 'MAT-100', Plant: '1000', StorageLocation: 'FG00' },
    ],
    unitsOfMeasure: [
      { Product: 'MAT-100', AlternativeUnit: 'EA', QuantityNumerator: 1, QuantityDenominator: 1 },
    ],
    inbound: {
      '1800000001': {
        DeliveryDocument: '1800000001',
        DeliveryDocumentType: 'EL',
        Supplier: 'V100',
        OverallGoodsMovementStatus: 'A',
        ActualGoodsMovementDate: null,
        MaterialDocument: null,
        MaterialDocumentYear: null,
        version: 1,
        items: [item('1800000001', 'MAT-100', '1.000', '101', '4500000001', '00010')],
      },
    },
    outbound: {
      '8000000001': {
        DeliveryDocument: '8000000001',
        DeliveryDocumentType: 'LF',
        ShipToParty: 'C100',
        OverallGoodsMovementStatus: 'A',
        ActualGoodsMovementDate: null,
        MaterialDocument: null,
        MaterialDocumentYear: null,
        version: 1,
        items: [item('8000000001', 'MAT-100', '1.000', '601', '', '')],
      },
    },
    materialDocuments: [],
    nextMaterialDocument: 5000000001,
    nextInbound: 1800000002,
    nextOutbound: 8000000002,
  }
}

function item(delivery, material, qty, movement, purchaseOrder, purchaseOrderItem) {
  return {
    DeliveryDocument: delivery,
    DeliveryDocumentItem: '000010',
    Material: material,
    Plant: '1000',
    StorageLocation: 'FG00',
    ActualDeliveryQuantity: qty,
    DeliveryQuantityUnit: 'EA',
    GoodsMovementStatus: 'A',
    GoodsMovementType: movement,
    PurchaseOrder: purchaseOrder,
    PurchaseOrderItem: purchaseOrderItem,
  }
}

let state = seed()

function reset() {
  state = seed()
}

function etag(doc) {
  return `W/"${doc.version}"`
}

function metadata(service, type, key) {
  return {
    uri: `${service}/${type}(${key})`,
    type: `${service.split('/').pop()}.${type}Type`,
  }
}

function viewDelivery(service, doc, withItems) {
  const { items, version, ...header } = doc
  const body = {
    __metadata: { ...metadata(service, service === INBOUND ? 'A_InbDeliveryHeader' : 'A_OutbDeliveryHeader', `'${doc.DeliveryDocument}'`), etag: etag(doc) },
    ...header,
  }
  if (withItems) body.to_DeliveryDocumentItem = { results: items.map((row) => ({ ...row })) }
  return body
}

function send(res, status, body, headers = {}) {
  const data = body == null ? '' : JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(data),
    'OData-Version': '2.0',
    ...headers,
  })
  res.end(data)
}

function sendError(res, status, message) {
  send(res, status, { error: { code: String(status), message: { lang: 'zh', value: message } } })
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw) return resolve({})
      try { resolve(JSON.parse(raw)) } catch (error) { reject(error) }
    })
  })
}

function unquote(value) {
  return value == null ? '' : String(value).replace(/^'+|'+$/g, '')
}

function matches(rows, filter) {
  if (!filter) return rows
  const found = filter.match(/^(\w+)\s+eq\s+'(.*)'$/)
  if (!found) return rows
  return rows.filter((row) => String(row[found[1]] ?? '') === found[2])
}

function collection(res, rows) {
  send(res, 200, { d: { results: rows } })
}

function single(res, row, tag, status = 200) {
  send(res, status, { d: row }, tag ? { ETag: tag } : {})
}

function parseSegment(segment) {
  const found = segment.match(/^([A-Za-z0-9_]+)(?:\((.*)\))?$/)
  if (!found) return null
  if (!found[2]) return { name: found[1], key: null }
  if (found[2].startsWith("'")) return { name: found[1], key: { value: unquote(found[2]) } }
  const key = {}
  for (const part of found[2].split(',')) {
    const [name, value] = part.split('=')
    key[name.trim()] = unquote(value)
  }
  return { name: found[1], key }
}

function qty(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return null
  return number.toFixed(3)
}

function postMovement(kind, doc) {
  const movement = kind === 'inbound' ? '101' : '601'
  const code = kind === 'inbound' ? '01' : '03'
  const number = String(state.nextMaterialDocument++)
  const year = String(new Date().getUTCFullYear())
  const header = {
    MaterialDocument: number,
    MaterialDocumentYear: year,
    GoodsMovementCode: code,
    DocumentDate: new Date().toISOString().slice(0, 10),
    PostingDate: new Date().toISOString().slice(0, 10),
    MaterialDocumentHeaderText: kind === 'inbound' ? `GR ${doc.DeliveryDocument}` : `GI ${doc.DeliveryDocument}`,
    ReferenceDocument: doc.DeliveryDocument,
  }
  header.to_MaterialDocumentItem = {
    results: doc.items.map((row, index) => ({
      MaterialDocument: number,
      MaterialDocumentYear: year,
      MaterialDocumentItem: String(index + 1),
      Material: row.Material,
      Plant: row.Plant,
      StorageLocation: row.StorageLocation,
      GoodsMovementType: movement,
      QuantityInEntryUnit: row.ActualDeliveryQuantity,
      EntryUnit: row.DeliveryQuantityUnit,
      PurchaseOrder: row.PurchaseOrder || '',
      PurchaseOrderItem: row.PurchaseOrderItem || '',
      ReferenceDocument: doc.DeliveryDocument,
      ReferenceDocumentItem: row.DeliveryDocumentItem,
    })),
  }
  state.materialDocuments.push(header)
  doc.OverallGoodsMovementStatus = 'C'
  doc.ActualGoodsMovementDate = header.PostingDate
  doc.MaterialDocument = number
  doc.MaterialDocumentYear = year
  doc.version += 1
  for (const row of doc.items) row.GoodsMovementStatus = 'C'
  return header
}

function requireMatch(res, doc, header) {
  if (!header) {
    sendError(res, 428, '过账必须带 If-Match')
    return false
  }
  if (header !== etag(doc)) {
    sendError(res, 412, `交货单 ${doc.DeliveryDocument} 已经被改过，先重新读取`)
    return false
  }
  return true
}

function serviceOf(pathname) {
  return Object.keys(SERVICES).find((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
}

async function handle(req, res) {
  const url = new URL(req.url, 'http://localhost')
  const pathname = decodeURIComponent(url.pathname)
  const service = serviceOf(pathname)
  if (!service) {
    if (pathname === '/health') return send(res, 200, { ok: true })
    return sendError(res, 404, `没有这个地址 ${pathname}`)
  }

  const csrf = req.headers['x-csrf-token']
  if (String(csrf || '').toLowerCase() === 'fetch') res.setHeader('x-csrf-token', token)
  if (req.method === 'HEAD') return res.writeHead(200).end()
  if (!['GET', 'OPTIONS'].includes(req.method) && csrf !== token) {
    res.setHeader('x-csrf-token', 'Required')
    return sendError(res, 403, 'CSRF token validation failed')
  }

  const rest = pathname.slice(service.length + 1)
  const query = Object.fromEntries(url.searchParams.entries())
  if (!rest || rest === '') {
    return send(res, 200, { d: { EntitySets: SERVICES[service] } })
  }
  if (rest === '$metadata') {
    res.writeHead(200, { 'Content-Type': 'application/xml; charset=utf-8' })
    res.end(metadataXml(service))
    return
  }

  if (rest === 'PostGoodsReceipt' || rest === 'PostGoodsIssue') {
    const kind = rest === 'PostGoodsReceipt' ? 'inbound' : 'outbound'
    const book = kind === 'inbound' ? state.inbound : state.outbound
    const expected = kind === 'inbound' ? INBOUND : OUTBOUND
    if (service !== expected) return sendError(res, 404, `${rest} 不属于这个服务`)
    const delivery = unquote(query.DeliveryDocument)
    const doc = book[delivery]
    if (!doc) return sendError(res, 404, `交货单 ${delivery} 不存在`)
    if (!requireMatch(res, doc, req.headers['if-match'])) return
    if (doc.OverallGoodsMovementStatus !== 'A') {
      return sendError(res, 400, `交货单 ${delivery} 的货物移动状态是 '${doc.OverallGoodsMovementStatus}'，只有未过账可以过账`)
    }
    const materialDocument = postMovement(kind, doc)
    const view = viewDelivery(service, doc, true)
    view.MaterialDocument = materialDocument.MaterialDocument
    view.MaterialDocumentYear = materialDocument.MaterialDocumentYear
    return single(res, view, etag(doc))
  }

  const [segment, navigation] = rest.split('/')
  const parsed = parseSegment(segment)
  if (!parsed) return sendError(res, 404, `无法解析 ${segment}`)

  if (req.method === 'GET') return getEntity(res, service, parsed, navigation, query)
  if (req.method === 'POST') return createEntity(req, res, service, parsed)
  return sendError(res, 405, '不支持这个方法')
}

function getEntity(res, service, parsed, navigation, query) {
  const filter = query['$filter']
  const expand = (query['$expand'] || '').includes('to_DeliveryDocumentItem')
  if (service === PRODUCT) return getProduct(res, parsed, filter)
  if (service === MATERIAL) return getMaterial(res, parsed, filter)
  const book = service === INBOUND ? state.inbound : state.outbound
  const itemSet = service === INBOUND ? 'A_InbDeliveryItem' : 'A_OutbDeliveryItem'
  const headerSet = service === INBOUND ? 'A_InbDeliveryHeader' : 'A_OutbDeliveryHeader'
  if (parsed.name === headerSet && !parsed.key) {
    return collection(res, matches(Object.values(book), filter).map((doc) => viewDelivery(service, doc, true)))
  }
  if (parsed.name === headerSet && parsed.key) {
    const doc = book[parsed.key.value]
    if (!doc) return sendError(res, 404, `交货单 ${parsed.key.value} 不存在`)
    if (navigation === 'to_DeliveryDocumentItem') return collection(res, doc.items.map((row) => ({ ...row })))
    return single(res, viewDelivery(service, doc, expand || true), etag(doc))
  }
  if (parsed.name === itemSet && !parsed.key) {
    const rows = Object.values(book).flatMap((doc) => doc.items)
    return collection(res, matches(rows, filter).map((row) => ({ ...row })))
  }
  if (parsed.name === itemSet && parsed.key) {
    const doc = book[parsed.key.DeliveryDocument]
    const row = doc && doc.items.find((item) => item.DeliveryDocumentItem === parsed.key.DeliveryDocumentItem)
    if (!row) return sendError(res, 404, '交货单行不存在')
    return single(res, { ...row })
  }
  return sendError(res, 404, `没有实体集 ${parsed.name}`)
}

function getProduct(res, parsed, filter) {
  const tables = {
    A_Product: state.products,
    A_ProductPlant: state.plants,
    A_ProductStorageLocation: state.storageLocations,
    A_ProductUnitsOfMeasure: state.unitsOfMeasure,
  }
  const rows = tables[parsed.name]
  if (!rows) return sendError(res, 404, `没有实体集 ${parsed.name}`)
  if (!parsed.key) return collection(res, matches(rows, filter).map((row) => ({ ...row })))
  const row = rows.find((item) => item.Product === (parsed.key.value || parsed.key.Product)
    && (parsed.key.Plant == null || item.Plant === parsed.key.Plant)
    && (parsed.key.StorageLocation == null || item.StorageLocation === parsed.key.StorageLocation)
    && (parsed.key.AlternativeUnit == null || item.AlternativeUnit === parsed.key.AlternativeUnit))
  if (!row) return sendError(res, 404, '主数据不存在')
  return single(res, { ...row })
}

function getMaterial(res, parsed, filter) {
  if (parsed.name === 'A_MaterialDocumentHeader' && !parsed.key) {
    return collection(res, matches(state.materialDocuments, filter).map(viewMaterial))
  }
  if (parsed.name === 'A_MaterialDocumentHeader' && parsed.key) {
    const header = state.materialDocuments.find((doc) => doc.MaterialDocument === parsed.key.MaterialDocument && doc.MaterialDocumentYear === parsed.key.MaterialDocumentYear)
    if (!header) return sendError(res, 404, '物料凭证不存在')
    return single(res, viewMaterial(header))
  }
  if (parsed.name === 'A_MaterialDocumentItem' && !parsed.key) {
    const rows = state.materialDocuments.flatMap((doc) => doc.to_MaterialDocumentItem.results)
    return collection(res, matches(rows, filter).map((row) => ({ ...row })))
  }
  return sendError(res, 404, `没有实体集 ${parsed.name}`)
}

function viewMaterial(header) {
  return {
    MaterialDocument: header.MaterialDocument,
    MaterialDocumentYear: header.MaterialDocumentYear,
    GoodsMovementCode: header.GoodsMovementCode,
    DocumentDate: header.DocumentDate,
    PostingDate: header.PostingDate,
    MaterialDocumentHeaderText: header.MaterialDocumentHeaderText,
    ReferenceDocument: header.ReferenceDocument,
    to_MaterialDocumentItem: { results: header.to_MaterialDocumentItem.results.map((row) => ({ ...row })) },
  }
}

async function createEntity(req, res, service, parsed) {
  let payload
  try { payload = await readBody(req) } catch { return sendError(res, 400, '请求体不是 JSON') }
  if (payload.d) payload = payload.d
  if (service === INBOUND && parsed.name === 'A_InbDeliveryHeader') return createDelivery(res, 'inbound', payload)
  if (service === OUTBOUND && parsed.name === 'A_OutbDeliveryHeader') return createDelivery(res, 'outbound', payload)
  if (service === MATERIAL && parsed.name === 'A_MaterialDocumentHeader') return createMaterial(res, payload)
  return sendError(res, 405, '这个实体集不能新建')
}

function createDelivery(res, kind, payload) {
  const nested = payload.to_DeliveryDocumentItem
  const rows = (nested && (nested.results || nested)) || []
  const list = Array.isArray(rows) ? rows : [rows]
  if (!list.length || !list[0].Material) return sendError(res, 400, '要带至少一行 to_DeliveryDocumentItem，并写 Material')
  const movement = kind === 'inbound' ? '101' : '601'
  const draftItems = list.map((row, index) => ({
    DeliveryDocumentItem: String((index + 1) * 10).padStart(6, '0'),
    Material: row.Material,
    Plant: row.Plant || '1000',
    StorageLocation: row.StorageLocation || 'FG00',
    ActualDeliveryQuantity: qty(row.ActualDeliveryQuantity || 1),
    DeliveryQuantityUnit: row.DeliveryQuantityUnit || 'EA',
    GoodsMovementStatus: 'A',
    GoodsMovementType: movement,
    PurchaseOrder: row.PurchaseOrder || '',
    PurchaseOrderItem: row.PurchaseOrderItem || '',
  }))
  if (draftItems.some((row) => !row.ActualDeliveryQuantity)) return sendError(res, 400, '数量必须大于 0')
  const book = kind === 'inbound' ? state.inbound : state.outbound
  const number = String(kind === 'inbound' ? state.nextInbound++ : state.nextOutbound++)
  const doc = {
    DeliveryDocument: number,
    DeliveryDocumentType: payload.DeliveryDocumentType || (kind === 'inbound' ? 'EL' : 'LF'),
    OverallGoodsMovementStatus: 'A',
    ActualGoodsMovementDate: null,
    MaterialDocument: null,
    MaterialDocumentYear: null,
    version: 1,
    items: draftItems.map((row) => ({ ...row, DeliveryDocument: number })),
  }
  if (kind === 'inbound') doc.Supplier = payload.Supplier || ''
  else doc.ShipToParty = payload.ShipToParty || ''
  book[number] = doc
  const service = kind === 'inbound' ? INBOUND : OUTBOUND
  return single(res, viewDelivery(service, doc, true), etag(doc), 201)
}

function createMaterial(res, payload) {
  const code = payload.GoodsMovementCode
  const kind = code === '01' ? 'inbound' : code === '03' ? 'outbound' : ''
  if (!kind) return sendError(res, 400, 'GoodsMovementCode 只接受 01（收货）或 03（发货）')
  const nested = payload.to_MaterialDocumentItem
  const rows = (nested && (nested.results || nested)) || []
  const list = Array.isArray(rows) ? rows : [rows]
  const reference = (list[0] && (list[0].ReferenceDocument || list[0].DeliveryDocument)) || ''
  const book = kind === 'inbound' ? state.inbound : state.outbound
  const doc = book[reference]
  if (!doc) return sendError(res, 400, '物料凭证要指向一张未过账的交货单，行上写 ReferenceDocument')
  if (doc.OverallGoodsMovementStatus !== 'A') return sendError(res, 400, `交货单 ${reference} 已经过账`)
  const header = postMovement(kind, doc)
  return single(res, viewMaterial(header))
}

function metadataXml(service) {
  const name = service.split('/').pop()
  const sets = SERVICES[service].map((set) => `      <EntitySet Name="${set}" EntityType="${name}.${set}Type"/>`).join('\n')
  const action = service === INBOUND
    ? `      <FunctionImport Name="PostGoodsReceipt" ReturnType="${name}.A_InbDeliveryHeaderType" EntitySet="A_InbDeliveryHeader" m:HttpMethod="POST"><Parameter Name="DeliveryDocument" Type="Edm.String" Mode="In"/></FunctionImport>`
    : service === OUTBOUND
      ? `      <FunctionImport Name="PostGoodsIssue" ReturnType="${name}.A_OutbDeliveryHeaderType" EntitySet="A_OutbDeliveryHeader" m:HttpMethod="POST"><Parameter Name="DeliveryDocument" Type="Edm.String" Mode="In"/></FunctionImport>`
      : ''
  return `<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices m:DataServiceVersion="2.0" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
    <Schema Namespace="${name}" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
      <EntityContainer Name="${name}_Entities" m:IsDefaultEntityContainer="true">
${sets}
${action}
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>`
}

function createServer() {
  return http.createServer((req, res) => {
    handle(req, res).catch((error) => {
      console.error(error)
      if (!res.headersSent) sendError(res, 500, '服务器内部错误')
    })
  })
}

if (require.main === module) {
  createServer().listen(PORT, '0.0.0.0', () => {
    console.log(`SAP ERP mock http://0.0.0.0:${PORT}`)
  })
}

module.exports = { createServer, reset, PRODUCT, INBOUND, OUTBOUND, MATERIAL }
