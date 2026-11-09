const http = require('http')

const target = process.env.TARGET || 'http://openwms-common:8120'

// TMS 2.0 的 Feign 客户端把 GET /v1/location-groups 解成单个 LocationGroupVO，
// Common 3.0 在不带 name 时返回数组。这里取一个元素，避免创建运输单时 502。
function pickGroup(data, url) {
  if (!Array.isArray(data)) return null
  const name = url.searchParams.get('name')
  if (name) {
    const match = data.find((item) => item && item.name === name)
    if (match) return match
  }
  return data[0] || null
}

async function forward(request, response) {
  const url = new URL(request.url, target)
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  const body = Buffer.concat(chunks)
  const headers = { ...request.headers, host: new URL(target).host }
  delete headers['content-length']
  const upstream = await fetch(url, {
    method: request.method,
    headers,
    body: body.length ? body : undefined,
  })
  let payload = Buffer.from(await upstream.arrayBuffer())
  const text = payload.toString('utf8')
  if (url.pathname.startsWith('/v1/location-groups') && text.trimStart().startsWith('[')) {
    const picked = pickGroup(JSON.parse(text), url)
    payload = Buffer.from(JSON.stringify(picked))
    console.log(`${request.method} ${url.pathname}${url.search} 返回数组，取 ${picked && picked.name}`)
  }
  const responseHeaders = {}
  upstream.headers.forEach((value, key) => {
    if (key !== 'transfer-encoding' && key !== 'content-length' && key !== 'content-encoding') {
      responseHeaders[key] = value
    }
  })
  responseHeaders['content-length'] = Buffer.byteLength(payload)
  response.writeHead(upstream.status, responseHeaders)
  response.end(payload)
}

const server = http.createServer((request, response) => {
  forward(request, response).catch((error) => {
    console.error(`${request.method} ${request.url} 转发失败：${error.message}`)
    if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end(error.message)
  })
})

server.listen(8120, '0.0.0.0')
