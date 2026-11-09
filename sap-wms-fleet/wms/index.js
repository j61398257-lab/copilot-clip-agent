const http = require('http')
const fs = require('fs')
const path = require('path')

const sap = process.env.SAP_URL || 'http://sap:4004'
const product = `${sap}/sap/opu/odata/sap/API_PRODUCT_SRV`
const inbound = `${sap}/sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002`
const wmsCommon = process.env.WMS_COMMON_URL || 'http://openwms-common:8120'
const wmsTms = process.env.WMS_TMS_URL || 'http://openwms-tms:8131'
const fleet = process.env.FLEET_URL || 'http://opentcs:55200'
const tuBarcode = process.env.TU_BARCODE || '00000000000000004711'
const vehicleName = process.env.VEHICLE_NAME || 'Vehicle-01'
const pollMs = Number(process.env.POLL_MS || 2000)

// SAP 只认识工厂和库存地点。库位对照是 WMS 自己的主数据。
const sites = {
  '1000/FG00': {
    receive: { openwms: 'FGIN/GR/0001/0000/0000', opentcs: 'GoodsReceipt', group: 'IPOINT1', plc: 'GR-DOCK' },
    store: { openwms: 'FGIN/ST/0001/0000/0000', opentcs: 'Storage-01', group: 'FGWORKPLACE1', plc: 'ST-01' },
  },
}

const journal = []
const inflight = new Set()
const links = {}
const parked = new Set()
let csrfToken = null

function note(message, extra) {
  const line = { at: new Date().toISOString(), message, ...(extra ? { extra } : {}) }
  journal.push(line)
  if (journal.length > 200) journal.shift()
  console.log(`${line.at} ${message}${extra ? ' ' + JSON.stringify(extra) : ''}`)
}

async function req(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {})
    }
  })
  const text = await response.text()
  let body = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = text }
  }
  if (!response.ok) {
    const error = new Error(`${options.method || 'GET'} ${url} -> ${response.status} ${typeof body === 'string' ? body : JSON.stringify(body)}`)
    error.status = response.status
    error.body = body
    error.headers = response.headers
    throw error
  }
  return { status: response.status, body, headers: response.headers }
}

async function waitUntil(label, probe, timeoutMs = 180000) {
  const started = Date.now()
  let last = ''
  while (Date.now() - started < timeoutMs) {
    try {
      await probe()
      note(`${label} 已就绪`)
      return
    } catch (error) {
      last = error.message
      await sleep(pollMs)
    }
  }
  throw new Error(`${label} 在 ${timeoutMs}ms 内没有就绪：${last}`)
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function results(body) {
  return (body && body.d && body.d.results) || []
}

async function ensureFleet() {
  const model = JSON.parse(fs.readFileSync(path.join(__dirname, 'plant-model.json'), 'utf8'))
  let current = null
  try {
    current = (await req(`${fleet}/v1/plantModel`)).body
  } catch (error) {
    note('读取工厂模型失败，准备上传', { error: error.message })
  }
  const names = new Set((current && current.locations || []).map((item) => item.name))
  if (!names.has('GoodsReceipt') || !names.has('Storage-01')) {
    await req(`${fleet}/v1/plantModel`, { method: 'PUT', body: JSON.stringify(model) })
    note('已上传 openTCS 工厂模型')
  }
  const adapter = 'org.opentcs.virtualvehicle.LoopbackCommunicationAdapterDescription'
  await req(`${fleet}/v1/vehicles/${vehicleName}/commAdapter/attachment?newValue=${encodeURIComponent(adapter)}`, { method: 'PUT' })
  await req(`${fleet}/v1/vehicles/${vehicleName}/commAdapter/enabled?newValue=true`, { method: 'PUT' })
  await req(`${fleet}/v1/vehicles/${vehicleName}/integrationLevel?newValue=TO_BE_UTILIZED`, { method: 'PUT' })
  note('Loopback 车辆已启用并可以接单', { vehicle: vehicleName })
}

async function ensureLocation(spec) {
  try {
    await req(`${wmsCommon}/v1/locations?locationId=${encodeURIComponent(spec.locationId)}`, {
      headers: { Accept: 'application/vnd.openwms.common.location-v1+json' },
    })
  } catch (error) {
    if (error.status !== 404) throw error
    await req(`${wmsCommon}/v1/locations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/vnd.openwms.common.location-v1+json',
        Accept: 'application/vnd.openwms.common.location-v1+json',
      },
      body: JSON.stringify(spec),
    })
    note('已创建 OpenWMS 库位', { locationId: spec.locationId })
  }
}

async function ensureWms() {
  for (const site of Object.values(sites)) {
    for (const place of [site.receive, site.store]) {
      await ensureLocation({
        locationId: place.openwms,
        plcCode: place.plc,
        erpCode: '1000/FG00',
        type: 'FG',
        locationGroupName: place.group,
      })
    }
  }
}

async function syncMasterData() {
  const products = results((await req(`${product}/A_Product`)).body)
  const slocs = results((await req(`${product}/A_ProductStorageLocation`)).body)
  note('已从 SAP 同步物料主数据和库存地点', {
    products: products.map((row) => `${row.Product}/${row.BaseUnit}`),
    storageLocations: slocs.map((row) => `${row.Plant}/${row.StorageLocation}`),
  })
}

async function moveTu(location) {
  try {
    await req(`${wmsCommon}/v1/transport-units?bk=${encodeURIComponent(tuBarcode)}`, {
      headers: { Accept: 'application/vnd.openwms.transport-unit-v1+json' },
    })
  } catch (error) {
    if (error.status !== 404) throw error
    await req(
      `${wmsCommon}/v1/transport-units?bk=${encodeURIComponent(tuBarcode)}&actualLocation=${encodeURIComponent(location)}&tut=PALLET`,
      { method: 'POST', headers: { Accept: 'application/vnd.openwms.transport-unit-v1+json' } },
    )
    note('已登记托盘', { barcode: tuBarcode, location })
    return
  }
  await req(`${wmsCommon}/v1/transport-units?bk=${encodeURIComponent(tuBarcode)}&newLocation=${encodeURIComponent(location)}`, {
    method: 'PATCH',
    headers: { Accept: 'application/vnd.openwms.transport-unit-v1+json' },
  })
}

async function createWmsOrder(target) {
  const response = await fetch(`${wmsTms}/v1/transport-orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ barcode: tuBarcode, targetLocation: target, priority: 'NORMAL' })
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`创建 OpenWMS 运输单失败 ${response.status} ${text}`)
  const location = response.headers.get('location') || ''
  const pKey = location.split('/').filter(Boolean).pop()
  if (!pKey) throw new Error(`OpenWMS 没有返回运输单地址：${location}`)
  return pKey
}

async function wmsState(pKey) {
  const { body } = await req(`${wmsTms}/v1/transport-orders/${pKey}`)
  return body.state || body.transportOrderState || ''
}

async function changeWmsState(pKey, state) {
  const current = await wmsState(pKey)
  if (current === state) return
  await req(`${wmsTms}/v1/transport-orders/${pKey}?state=${state}`, { method: 'POST' })
}

async function createFleetOrder(name, src, dst) {
  await req(`${fleet}/v1/transportOrders/${encodeURIComponent(name)}`, {
    method: 'POST',
    body: JSON.stringify({
      intendedVehicle: vehicleName,
      destinations: [
        { locationName: src.opentcs, operation: 'Load cargo' },
        { locationName: dst.opentcs, operation: 'Unload cargo' }
      ]
    })
  })
}

async function fleetState(name) {
  const { body } = await req(`${fleet}/v1/transportOrders/${encodeURIComponent(name)}`)
  return body.state
}

function fleetName(delivery) {
  return `INB-${delivery}`
}

function park(delivery, text) {
  parked.add(delivery)
  links[delivery] = { ...links[delivery], problem: text }
  note('入库交货停手，SAP 仍未过账，等人处理', { delivery, text })
}

async function fetchCsrf() {
  const response = await fetch(`${inbound}/`, { method: 'HEAD', headers: { 'X-CSRF-Token': 'Fetch' } })
  csrfToken = response.headers.get('x-csrf-token')
  if (!csrfToken) throw new Error(`SAP 没有返回 CSRF 令牌：${response.status}`)
}

async function postGoodsReceipt(delivery) {
  const fresh = await req(`${inbound}/A_InbDeliveryHeader('${delivery}')`)
  const tag = fresh.headers.get('etag')
  if (!csrfToken) await fetchCsrf()
  const send = () => req(`${inbound}/PostGoodsReceipt?DeliveryDocument='${delivery}'`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken, 'If-Match': tag },
    body: '{}',
  })
  try {
    return (await send()).body.d
  } catch (error) {
    if (error.status !== 403) throw error
    await fetchCsrf()
    return (await send()).body.d
  }
}

async function watchFleet(name) {
  const deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    const state = await fleetState(name)
    if (state === 'FINISHED' || state === 'FAILED' || state === 'UNROUTABLE') return state
    await sleep(pollMs)
  }
  return 'TIMEOUT'
}

async function accept(header) {
  const delivery = header.DeliveryDocument
  if (inflight.has(delivery) || parked.has(delivery)) return
  const line = (header.to_DeliveryDocumentItem && header.to_DeliveryDocumentItem.results || [])[0]
  if (!line) {
    park(delivery, '入库交货没有行')
    return
  }
  const site = sites[`${line.Plant}/${line.StorageLocation}`]
  if (!site) {
    park(delivery, `没有工厂/库存地点映射：${line.Plant}/${line.StorageLocation}`)
    return
  }
  inflight.add(delivery)
  const name = fleetName(delivery)
  note('接手 SAP 入库交货', {
    delivery,
    material: line.Material,
    qty: line.ActualDeliveryQuantity,
    plant: line.Plant,
    storageLocation: line.StorageLocation,
  })
  try {
    await moveTu(site.receive.openwms)
    const pKey = await createWmsOrder(site.store.openwms)
    links[delivery] = { wms: pKey, fleet: name, material: line.Material }
    note('已在 OpenWMS 登记托盘并创建上架运输单', { pKey, from: site.receive.openwms, to: site.store.openwms })
    try { await changeWmsState(pKey, 'STARTED') } catch (error) {
      note('显式启动运输单没有成功，继续使用当前状态', { error: error.message })
    }
    await createFleetOrder(name, site.receive, site.store)
    note('已下发 openTCS 上架订单', { name })
    const state = await watchFleet(name)
    if (state !== 'FINISHED') {
      try { await changeWmsState(pKey, 'ONFAILURE') } catch (error) {
        note('OpenWMS 置为失败没有成功', { error: error.message })
      }
      park(delivery, state === 'TIMEOUT' ? '等待 openTCS 超时' : `openTCS 运输单结束于 ${state}`)
      return
    }
    await changeWmsState(pKey, 'FINISHED')
    await moveTu(site.store.openwms)
    const posted = await postGoodsReceipt(delivery)
    links[delivery].materialDocument = posted.MaterialDocument
    note('上架完成，已在 SAP 过账收货', { delivery, materialDocument: posted.MaterialDocument })
  } catch (error) {
    park(delivery, error.message.slice(0, 240))
  } finally {
    inflight.delete(delivery)
  }
}

async function loop() {
  const open = results((await req(`${inbound}/A_InbDeliveryHeader?$filter=${encodeURIComponent("OverallGoodsMovementStatus eq 'A'")}`)).body)
  for (const header of open) {
    if (inflight.size > 0) break
    await accept(header)
  }
}

function startStatusServer() {
  const server = http.createServer((request, response) => {
    if (request.url === '/health') {
      response.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
      response.end('ok')
      return
    }
    response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ inflight: [...inflight], parked: [...parked], links, journal: journal.slice(-40) }, null, 2))
  })
  server.listen(8090, '0.0.0.0')
}

async function main() {
  startStatusServer()
  note('WMS 启动，准备直接调用 SAP')
  await waitUntil('SAP', () => req(`${inbound}/$metadata`))
  await waitUntil('OpenWMS Common', () => req(`${wmsCommon}/actuator/info`))
  await waitUntil('OpenWMS TMS', () => req(`${wmsTms}/actuator/info`))
  await waitUntil('openTCS', () => req(`${fleet}/v1/kernel/version`))
  await syncMasterData()
  await ensureWms()
  await ensureFleet()
  note('开始轮询 SAP 未过账的入库交货')
  for (;;) {
    try {
      await loop()
    } catch (error) {
      note('轮询出错，下一轮再试', { error: error.message })
    }
    await sleep(pollMs)
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
