import http from 'node:http'

// Bind on all local interfaces by default so the proxy survives Wi-Fi changes.
// Docker Desktop reaches the host through host.docker.internal.
const bindHost = process.env.SAG_DSH_PROXY_HOST ?? '0.0.0.0'
const bindPort = Number(process.env.SAG_DSH_PROXY_PORT ?? '3081')
const upstreamPort = Number(process.env.SAG_DSH_UPSTREAM_PORT ?? process.env.DSH_WEB_PORT ?? '3080')
const gatewayPath = '/ext/sag-agent-gateway'

if (!Number.isInteger(bindPort) || bindPort < 1 || bindPort > 65535 || !Number.isInteger(upstreamPort) || upstreamPort < 1 || upstreamPort > 65535) {
  throw new Error('SAG_DSH_PROXY_PORT and SAG_DSH_UPSTREAM_PORT must be valid TCP ports.')
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
    port: upstreamPort,
    method: request.method,
    path: request.url,
    headers: { ...request.headers, host: `127.0.0.1:${upstreamPort}` },
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
  process.stdout.write(`SAG DSH gateway proxy listening on http://${bindHost}:${bindPort}${gatewayPath} -> 127.0.0.1:${upstreamPort}\n`)
})
