import http from 'node:http'

const bindHost = process.env.SAG_DSH_PROXY_HOST
const bindPort = Number(process.env.SAG_DSH_PROXY_PORT ?? '3081')
const gatewayPath = '/ext/sag-agent-gateway'

if (!bindHost || !Number.isInteger(bindPort) || bindPort < 1 || bindPort > 65535) {
  throw new Error('Set SAG_DSH_PROXY_HOST to the host IPv4 address and SAG_DSH_PROXY_PORT to a valid port.')
}

function isGatewayRequest(request) {
  const pathname = new URL(request.url ?? '/', 'http://localhost').pathname
  return pathname === gatewayPath || pathname.startsWith(`${gatewayPath}/`)
}

const server = http.createServer((request, response) => {
  if (!isGatewayRequest(request) || !['GET', 'POST'].includes(request.method ?? '')) {
    response.writeHead(404, { 'content-type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ error: 'not found' }))
    return
  }

  const upstream = http.request({
    host: '127.0.0.1',
    port: 3080,
    method: request.method,
    path: request.url,
    headers: { ...request.headers, host: '127.0.0.1:3080' },
  }, upstreamResponse => {
    response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers)
    upstreamResponse.pipe(response)
  })

  upstream.on('error', error => {
    if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ error: `DSH gateway unavailable: ${error.message}` }))
  })
  request.pipe(upstream)
})

server.listen(bindPort, bindHost, () => {
  process.stdout.write(`SAG DSH gateway proxy listening on http://${bindHost}:${bindPort}${gatewayPath}\n`)
})
