import { timingSafeEqual } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from 'cordis'
import z from 'schemastery'
import { createAssistantMessage, createUserMessage, type ContentBlock } from '@deepseek-ai/dsh-llm'

type ModelInfo = {
  id: string
  name: string
  description?: string
  inputModalities?: readonly string[]
  context?: { contextWindow?: number }
  reasoning?: { efforts?: readonly { id: string; name: string; description?: string }[]; defaultEffort?: string }
}

type GatewayContext = Context & {
  logger?: { info?(message: string): void }
  tools: { register(definition: unknown): void }
  skills: { register(skill: Record<string, unknown>): () => void }
  web?: { search(request: { query: string; maxResults?: number }, signal?: AbortSignal): Promise<WebSearchResult> }
  webServer?: { register(route: { kind: 'exact' | 'prefix'; path: string; handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void> }): () => void }
  llm?: { listProviders(): Array<{ id: string; name: string }>; listModels(provider: string): Promise<readonly ModelInfo[]>; stream(options: Record<string, unknown>): AsyncIterable<Record<string, unknown>> }
  agents?: { create(options: Record<string, unknown>): Promise<{ agent: GatewayAgent; dispose(): Promise<void> }> }
  attachments?: { saveImages(inputs: readonly { data: Uint8Array; mediaType: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'; name?: string }[]): Promise<readonly unknown[]> }
  permissionPresets?: { names: readonly string[]; resolve(name: string): unknown; set(session: unknown, name: string): void }
  tokenMeter?: { measure(session: unknown): Record<string, unknown> }
}

type GatewayAgent = {
  session: { events: Array<{ seq: number; type: string; data: Record<string, unknown> }> }
  ctx: { tools?: { register(definition: unknown): () => void; restrict(filter: { allow: string[] }): () => void } }
  followup(message: unknown): void
  cancel(cause: { kind: 'user' }, options?: { keepInbox?: boolean }): void
  whenIdle(): Promise<void>
}

type Evidence = { source_id: string; source_name: string; document_name?: string; chunk_id?: string; heading: string; content: string; score?: number }
type WebSource = { url: string; title?: string; snippet?: string; publishedAt?: string }
type WebSearchResult = { sources: readonly WebSource[]; content?: string; truncated: boolean }
type ImageRef = { attachmentId: string; mediaType: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'; bytes: number; width: number; height: number; name?: string }

export const name = '@dsh-external/dsh-sag-knowledge'
// The gateway owns a DSH WebServer route and drives DSH services directly.
// Declaring these dependencies makes the dynamically injected plugin receive
// the same concrete service set as a normal DSH bundle.
export const inject = ['tools', 'skills', 'web', 'webServer', 'llm', 'agents', 'attachments', 'permissionPresets', 'tokenMeter']

export interface Config {
  sagApiBase: string
  sagWebBase: string
  loginName: string
  autoLogin: boolean
  defaultTopK: number
  gatewayPath: string
  gatewayToken: string
  gatewayMaxBodyBytes: number
  maxResearchRounds: number
  contextTokenBudget: number
}

export const Config = z.object({
  sagApiBase: z.string().default('http://127.0.0.1:8000'),
  sagWebBase: z.string().default('http://127.0.0.1:3000'),
  loginName: z.string().default('dsh-sag'),
  autoLogin: z.boolean().default(true),
  defaultTopK: z.number().min(1).max(20).default(8),
  gatewayPath: z.string().default('/ext/sag-agent-gateway'),
  gatewayToken: z.string().default(''),
  // Four SAG attachments are limited to 10 MiB each. This is an upper-bound
  // for the private SAG-to-DSH hop, not a browser request limit.
  gatewayMaxBodyBytes: z.number().min(131_072).max(67_108_864).default(58_720_256),
  maxResearchRounds: z.number().min(1).max(5).default(5),
  contextTokenBudget: z.number().min(2_000).max(32_000).default(12_000),
})

class UnauthorizedError extends Error {
  constructor() { super('SAG token expired'); this.name = 'UnauthorizedError' }
}

async function sagRequest(base: string, path: string, init: RequestInit = {}): Promise<unknown> {
  const res = await fetch(base + path, { ...init, headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) } })
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) throw new Error(`SAG ${init.method ?? 'GET'} ${path} -> ${res.status} ${(await res.text().catch(() => '')).slice(0, 200)}`)
  return res.json() as Promise<unknown>
}

function writeJson(res: ServerResponse, status: number, value: unknown): void {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(value))
}

function authorized(req: IncomingMessage, secret: string): boolean {
  const header = req.headers.authorization
  if (!header?.startsWith('Bearer ')) return false
  const received = Buffer.from(header.slice(7), 'utf8')
  const expected = Buffer.from(secret, 'utf8')
  return received.length === expected.length && timingSafeEqual(received, expected)
}

function gatewayToken(config: Config): string {
  if (config.gatewayToken.trim()) return config.gatewayToken.trim()
  if (process.env.DSH_SAG_GATEWAY_TOKEN?.trim()) return process.env.DSH_SAG_GATEWAY_TOKEN.trim()
  // Runtime-injected plugins do not receive the profile's Cordis config. A
  // mode-0600 token file keeps that secret outside the repository and lets the
  // same package run both as a normal bundle and through super-injector.
  const file = process.env.DSH_SAG_GATEWAY_TOKEN_FILE || join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'sag-gateway.token')
  try { return readFileSync(file, 'utf8').trim() } catch { return '' }
}

async function readJson(req: IncomingMessage, limit: number): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    size += value.length
    if (size > limit) throw new Error('request body exceeds gateway limit')
    chunks.push(value)
  }
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('JSON object required')
  return parsed as Record<string, unknown>
}

function asString(value: unknown, field: string, max = 20_000): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(`${field} is required`)
  return value.trim()
}

function asStringList(value: unknown, field: string, max: number): string[] {
  if (value === undefined) return []
  if (!Array.isArray(value) || value.length > max || value.some(item => typeof item !== 'string' || !item.trim())) throw new Error(`${field} must be a list of strings`)
  return [...new Set(value.map(item => item.trim()))]
}

function history(value: unknown, maxTokens = 4_000): Array<{ role: 'user' | 'assistant'; content: string }> {
  if (!Array.isArray(value)) return []
  const rows: Array<{ role: 'user' | 'assistant'; content: string }> = value.slice(-20).flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const row = item as Record<string, unknown>
    return (row.role === 'user' || row.role === 'assistant') && typeof row.content === 'string' && row.content.trim()
      ? [{ role: row.role, content: row.content.slice(0, 16_000) }]
      : []
  })
  const kept: Array<{ role: 'user' | 'assistant'; content: string }> = []
  let remaining = Math.max(1, maxTokens) * 4
  for (let index = rows.length - 1; index >= 0 && remaining > 0; index -= 1) {
    const content = rows[index].content.slice(0, remaining)
    kept.unshift({ ...rows[index], content })
    remaining -= content.length
  }
  return kept
}

function clipTokens(value: string, maxTokens: number): string {
  return value.slice(0, Math.max(1, maxTokens) * 4)
}

function sseStart(res: ServerResponse): void {
  res.statusCode = 200
  res.setHeader('content-type', 'text/event-stream; charset=utf-8')
  res.setHeader('cache-control', 'no-cache, no-transform')
  res.setHeader('connection', 'keep-alive')
  res.flushHeaders()
}

function sse(res: ServerResponse, type: string, data: Record<string, unknown>): void {
  res.write(`event: ${type}\ndata: ${JSON.stringify({ type, timestamp: new Date().toISOString(), ...data })}\n\n`)
}

function citations(response: unknown): Evidence[] {
  if (!response || typeof response !== 'object' || Array.isArray(response)) return []
  const sections = (response as { sections?: unknown }).sections
  if (!Array.isArray(sections)) return []
  return sections.slice(0, 20).flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const row = item as Record<string, unknown>
    return [{
      source_id: typeof row.source_id === 'string' ? row.source_id : '',
      source_name: typeof row.source_name === 'string' ? row.source_name : '',
      document_name: typeof row.document_name === 'string' ? row.document_name : undefined,
      chunk_id: typeof row.chunk_id === 'string' ? row.chunk_id : undefined,
      heading: typeof row.heading === 'string' ? row.heading.slice(0, 500) : '',
      content: typeof row.content === 'string' ? row.content.slice(0, 12_000) : '',
      score: typeof row.score === 'number' ? row.score : undefined,
    }]
  })
}

function citationSummary(items: Evidence[]): Array<Record<string, unknown>> {
  return items.map(item => ({ source_id: item.source_id, source_name: item.source_name, document_name: item.document_name, chunk_id: item.chunk_id, heading: item.heading, score: item.score }))
}

function externalCitationSummary(items: readonly WebSource[]): Array<Record<string, unknown>> {
  const seen = new Set<string>()
  return items.flatMap(item => {
    let url: URL
    try { url = new URL(item.url) } catch { return [] }
    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || seen.has(url.href)) return []
    seen.add(url.href)
    return [{
      kind: 'external',
      url: url.href,
      title: item.title?.slice(0, 500) || url.hostname,
      summary: item.snippet?.slice(0, 1500) || '',
      source: '联网搜索',
    }]
  })
}

function evidenceFingerprint(item: Evidence): string {
  return `${item.source_id}\n${item.heading}\n${item.content}`
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .slice(0, 320)
}

function mergeEvidence(current: Evidence[], next: Evidence[]): Evidence[] {
  const merged = [...current]
  const seen = new Set(current.flatMap(item => [
    `${item.source_id}:${item.chunk_id ?? ''}:${item.heading}`,
    `fingerprint:${evidenceFingerprint(item)}`,
  ]))
  for (const item of next) {
    const key = `${item.source_id}:${item.chunk_id ?? ''}:${item.heading}`
    const fingerprint = `fingerprint:${evidenceFingerprint(item)}`
    if (!seen.has(key) && !seen.has(fingerprint)) {
      seen.add(key)
      seen.add(fingerprint)
      merged.push(item)
    }
  }
  return merged
}

function webEvidenceText(items: readonly WebSource[], maxTokens = 3_000): string {
  return items.map((item, index) => {
    const label = item.title?.trim() || `Web source ${index + 1}`
    return `[#W${index + 1}] [${label}](${item.url})\n${item.snippet?.trim() || '(No excerpt returned.)'}`
  }).join('\n\n').slice(0, maxTokens * 4)
}

type WebSearchOutcome = { sources: readonly WebSource[]; error?: string }

function describeWebSearchFailure(error: unknown, timedOut: boolean): string {
  if (timedOut) {
    return 'DSH 联网搜索在 35 秒内未响应。请检查 DSH 的搜索服务连接；本次已降级为知识库检索。'
  }
  const detail = error instanceof Error ? error.message : String(error)
  if (/insufficient\s*balance|余额不足|credit\s*(?:is\s*)?insufficient/i.test(detail)) {
    return 'DSH 联网搜索服务余额不足。请在 DSH 的联网搜索服务配置中充值或切换可用服务；本次已降级为知识库检索。'
  }
  if (/quota|rate[ -]?limit|too many requests/i.test(detail)) {
    return 'DSH 联网搜索服务额度已用尽或请求受限。请检查 DSH 的联网搜索服务配置；本次已降级为知识库检索。'
  }
  return detail
}

async function searchWeb(ctx: GatewayContext, query: string, res: ServerResponse): Promise<WebSearchOutcome> {
  if (!ctx.web) {
    const error = 'DSH 联网搜索服务未加载，请在 DSH 中启用 web-search 配置。'
    sse(res, 'web_search.failed', { query, error })
    return { sources: [], error }
  }
  sse(res, 'web_search.started', { query })
  const controller = new AbortController()
  const deadline = setTimeout(() => controller.abort(), 35_000)
  try {
    const result = await ctx.web.search({ query, maxResults: 8 }, controller.signal)
    const sources = result.sources.filter(item => typeof item.url === 'string' && item.url.length > 0)
    sse(res, 'web_search.completed', { query, web_hits: sources.length, citations: externalCitationSummary(sources) })
    return { sources }
  } catch (error) {
    const detail = describeWebSearchFailure(error, controller.signal.aborted)
    sse(res, 'web_search.failed', { query, error: detail })
    return { sources: [], error: `联网搜索失败：${detail}` }
  } finally {
    clearTimeout(deadline)
  }
}

function formatEvidence(items: readonly Evidence[], maxTokens: number): string {
  const pieces: string[] = []
  let remaining = Math.max(1, maxTokens) * 4
  for (let index = 0; index < items.length && remaining > 0; index += 1) {
    const item = items[index]
    const piece = `[#${index + 1}] ${item.source_name || item.source_id}\n${item.heading}\n${item.content}`
    const clipped = piece.slice(0, remaining)
    pieces.push(clipped)
    remaining -= clipped.length
  }
  return pieces.join('\n\n')
}

function fallbackResearchQuery(query: string, round: number): string {
  const normalized = query.replace(/[\n\r]+/g, ' ').replace(/\s+/g, ' ').trim()
  const focus = normalized.split(/[，,。；;!?！？]/).filter(Boolean).slice(0, 2).join(' ')
  const suffix = round % 3 === 1 ? '关键事实 时间线 数据' : round % 3 === 2 ? '背景 原因 影响 来源' : '对比 证据 最新进展'
  return `${focus || normalized} ${suffix}`.slice(0, 2_000)
}

async function rewriteResearchQuery(ctx: GatewayContext, query: string, round: number, provider: string, model: string): Promise<{ query: string; rationale: string }> {
  const fallback = fallbackResearchQuery(query, round)
  if (!ctx.llm) return { query: fallback, rationale: 'fallback' }
  let output = ''
  try {
    for await (const chunk of ctx.llm.stream({
      provider,
      model,
      maxTokens: 220,
      system: '你是知识库研究查询改写器。只输出 JSON：{"query":"...","rationale":"..."}。query 必须保留原问题的核心实体，补充下一轮需要验证的事实维度，不得引入原问题没有的具体事实。',
      messages: [textMessage('user', `原问题：${query}\n当前轮次：${round}\n请生成下一轮检索 query。`, provider, model)],
    })) {
      output += chunkText(chunk)
      if (output.length > 2_000) break
    }
    const match = output.match(/\{[\s\S]*\}/)
    if (match) {
      const parsed = JSON.parse(match[0]) as { query?: unknown; rationale?: unknown }
      if (typeof parsed.query === 'string' && parsed.query.trim()) {
        return { query: parsed.query.trim().slice(0, 2_000), rationale: typeof parsed.rationale === 'string' ? parsed.rationale.slice(0, 500) : 'model_rewrite' }
      }
    }
  } catch {
    // Rewriting is optional; retrieval remains available through the fallback.
  }
  return { query: fallback, rationale: 'fallback' }
}

function evidenceCoverage(items: readonly Evidence[], query: string): number {
  const terms = [...new Set(query.toLocaleLowerCase().match(/[\p{L}\p{N}]{2,}/gu) ?? [])].slice(0, 24)
  if (!terms.length) return 0
  const text = items.map(item => `${item.heading} ${item.content}`.toLocaleLowerCase()).join(' ')
  return terms.filter(term => text.includes(term)).length / terms.length
}

function evidenceSufficient(items: readonly Evidence[], query: string, round: number, maxRounds: number): { sufficient: boolean; coverage: number; sources: number; strong: number } {
  const sources = new Set(items.map(item => item.source_id).filter(Boolean))
  const strong = items.filter(item => typeof item.score !== 'number' || item.score >= 0.35).length
  const coverage = evidenceCoverage(items, query)
  return { sufficient: (sources.size >= 2 && strong >= 3 && coverage >= 0.35) || round >= maxRounds, coverage, sources: sources.size, strong }
}

/** Keep Agent tool results small enough for a second model step to run reliably. */
function compactRetrieval(response: Record<string, unknown>): Record<string, unknown> {
  const sections = Array.isArray(response.sections)
    ? response.sections.slice(0, 8).flatMap(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return []
      const row = item as Record<string, unknown>
      return [{
        chunk_id: typeof row.chunk_id === 'string' ? row.chunk_id : undefined,
        source_id: typeof row.source_id === 'string' ? row.source_id : undefined,
        source_name: typeof row.source_name === 'string' ? row.source_name : undefined,
        document_name: typeof row.document_name === 'string' ? row.document_name : undefined,
        heading: typeof row.heading === 'string' ? row.heading.slice(0, 300) : '',
        content: typeof row.content === 'string' ? row.content.slice(0, 3_500) : '',
        score: typeof row.score === 'number' ? row.score : undefined,
      }]
    })
    : []
  return { query: response.query, sections, source_hits: response.source_hits ?? [], stats: response.stats ?? {} }
}

async function scopedSearch(config: Config, delegationToken: string, query: string, sourceIds: string[], topK: number): Promise<Record<string, unknown>> {
  if (!sourceIds.length) return { query, sections: [], stats: { sources: 0, reason: 'no_source_scope' } }
  const response = await fetch(`${config.sagApiBase.replace(/\/$/, '')}/api/v1/dsh/retrieval`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${delegationToken}` },
    body: JSON.stringify({ query, source_ids: sourceIds, top_k: Math.min(Math.max(topK, 1), 20) }),
  })
  if (!response.ok) throw new Error(`SAG retrieval failed (${response.status})`)
  return await response.json() as Record<string, unknown>
}

async function decodeImages(ctx: GatewayContext, value: unknown): Promise<unknown[]> {
  if (!Array.isArray(value) || value.length === 0) return []
  if (!ctx.attachments) throw new Error('DSH attachment service is unavailable')
  if (value.length > 4) throw new Error('too many images')
  const decoded = value.map(raw => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('invalid image payload')
    const image = raw as Record<string, unknown>
    const mediaType = image.media_type
    const data = image.data
    if (mediaType !== 'image/png' && mediaType !== 'image/jpeg' && mediaType !== 'image/webp' && mediaType !== 'image/gif') throw new Error('unsupported image media type')
    if (typeof data !== 'string' || !/^[A-Za-z0-9+/]*={0,2}$/.test(data) || data.length > 14_000_000) throw new Error('invalid image data')
    const bytes = Buffer.from(data, 'base64')
    if (!bytes.length || bytes.length > 10 * 1024 * 1024) throw new Error('image exceeds 10 MiB limit')
    return { data: new Uint8Array(bytes), mediaType: mediaType as 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif', name: typeof image.name === 'string' ? image.name.slice(0, 300) : undefined }
  })
  return [...await ctx.attachments.saveImages(decoded)] as ImageRef[]
}

function textMessage(role: 'user' | 'assistant', content: string, provider: string, model: string): Record<string, unknown> {
  const blocks: ContentBlock[] = [{ type: 'text', text: content }]
  if (role === 'assistant') {
    return createAssistantMessage({ content: blocks, source: { provider, model } }) as unknown as Record<string, unknown>
  }
  return createUserMessage({ content: blocks, source: { kind: 'plugin', plugin: name } }) as unknown as Record<string, unknown>
}

function messageText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const row = value as Record<string, unknown>
    if (row.message && typeof row.message === 'object') return messageText(row.message)
    if (typeof row.text === 'string') return row.text
    if (typeof row.content === 'string') return row.content
    if (Array.isArray(row.content)) return row.content.flatMap(part => part && typeof part === 'object' && (part as Record<string, unknown>).type === 'text' && typeof (part as Record<string, unknown>).text === 'string' ? [(part as Record<string, unknown>).text as string] : []).join('')
  }
  return ''
}

function chunkText(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return ''
  const row = value as Record<string, unknown>
  if (typeof row.text === 'string' && (row.type === 'text-delta' || row.type === 'text')) return row.text
  if (row.chunk && typeof row.chunk === 'object') return chunkText(row.chunk)
  return ''
}

async function modelCatalog(ctx: GatewayContext): Promise<Record<string, unknown>> {
  if (!ctx.llm) throw new Error('DSH LLM service is unavailable')
  const groups = await Promise.all(ctx.llm.listProviders().map(async provider => ({
    id: provider.id,
    name: provider.name,
    models: (await ctx.llm!.listModels(provider.id)).map(model => ({
      id: model.id,
      name: model.name,
      description: model.description,
      input_modalities: model.inputModalities ?? [],
      context_window: (model as unknown as { context?: { contextWindow?: number } }).context?.contextWindow,
      reasoning_efforts: (model as unknown as { reasoning?: { efforts?: readonly { id: string; name: string; description?: string }[] } }).reasoning?.efforts ?? [],
      default_reasoning_effort: (model as unknown as { reasoning?: { defaultEffort?: string } }).reasoning?.defaultEffort,
    })),
  })))
  return { groups, permission_presets: [...(ctx.permissionPresets?.names ?? [])], modes: ['controlled', 'agent'] }
}

async function streamControlledAnswer(ctx: GatewayContext, config: Config, res: ServerResponse, body: Record<string, unknown>, options: { alreadyStarted?: boolean; routeMode?: string; degradedReason?: string; req?: IncomingMessage } = {}): Promise<void> {
  if (!ctx.llm) throw new Error('DSH LLM service is unavailable')
  const query = asString(body.query, 'query', 4000)
  const provider = asString(body.provider, 'provider', 200)
  const model = asString(body.model, 'model', 300)
  const sourceIds = asStringList(body.source_ids, 'source_ids', 8)
  const delegationToken = asString(body.delegation_token, 'delegation_token', 10_000)
  const previous = history(body.history, Math.floor(config.contextTokenBudget * 0.3))
  const imageRefs = await decodeImages(ctx, body.images) as ImageRef[]
  const cancellation = new AbortController()
  options.req?.once('aborted', () => cancellation.abort())
  if (!options.alreadyStarted) {
    sseStart(res)
    sse(res, 'run.started', { mode: 'controlled', source_ids: sourceIds, session_id: typeof body.session_id === 'string' ? body.session_id : '' })
  }
  let text = ''
  let usage: Record<string, unknown> = {}
  try {
    sse(res, 'research.started', { max_rounds: 1, query })
    sse(res, 'retrieval.started', { query, source_ids: sourceIds, round: 1 })
    const retrieval = await scopedSearch(config, delegationToken, query, sourceIds, config.defaultTopK)
    if (cancellation.signal.aborted) { sse(res, 'run.cancelled', { reason: 'client_disconnected' }); return }
    const evidence = citations(retrieval)
    sse(res, 'retrieval.completed', { query, retrieval_hits: evidence.length, citations: citationSummary(evidence), stats: retrieval.stats ?? {} })
    const sufficiency = evidenceSufficient(evidence, query, 1, 1)
    sse(res, 'research.progress', { round: 1, evidence_hits: evidence.length, source_count: sufficiency.sources, strong_hits: sufficiency.strong, coverage: Number(sufficiency.coverage.toFixed(3)), sufficient: sufficiency.sufficient })
    sse(res, 'research.completed', { rounds: 1, evidence_hits: evidence.length, coverage: Number(sufficiency.coverage.toFixed(3)), reason: 'controlled_rag' })
    const webOutcome = body.web_enabled ? await searchWeb(ctx, query, res) : { sources: [] as readonly WebSource[] }
    const webSources = webOutcome.sources
    const degradedReason = options.degradedReason || webOutcome.error
    if (degradedReason) sse(res, 'run.degraded', { from: 'web_search', to: 'knowledge_only', reason: degradedReason })
    const evidenceText = formatEvidence(evidence, config.contextTokenBudget)
    const onlineEvidence = webSources.length ? webEvidenceText(webSources, Math.floor(config.contextTokenBudget * 0.2)) : '(Online search was unavailable; answer from SAG knowledge evidence only.)'
    const messages = [
      ...previous.map(item => textMessage(item.role, item.content, provider, model)),
      createUserMessage({
        content: ([{ type: 'text', text: `Question:\n${query}\n\nKnowledge evidence:\n${evidenceText || '(No SAG knowledge source was selected or no evidence was found.)'}${body.web_enabled ? `\n\nOnline evidence:\n${onlineEvidence}` : ''}` }, ...imageRefs.map(attachment => ({ type: 'image', attachment }))] as unknown as ContentBlock[]),
        source: { kind: 'plugin', plugin: name },
      }),
    ]
    for await (const chunk of ctx.llm.stream({
      provider,
      model,
      ...(typeof body.reasoning_effort === 'string' ? { reasoningEffort: body.reasoning_effort } : {}),
      maxTokens: 4096,
      signal: cancellation.signal,
      system: `You are the DSH answer layer inside SAG. Use the supplied SAG evidence for knowledge-base claims and cite it as [source name].${body.web_enabled ? ' Online evidence was explicitly searched for this turn: synthesize it with the knowledge evidence, distinguish conflicts or uncertainty, and cite every web claim with its exact markdown URL.' : ''} If evidence is insufficient, state that clearly. Do not claim access to tools, files, or web results not present in this turn.`,
      messages,
    })) {
      if (cancellation.signal.aborted) { sse(res, 'run.cancelled', { reason: 'client_disconnected' }); return }
      const streamChunk = chunk as {
        type?: string
        reason?: { kind?: string; failure?: { message?: string } }
        block?: { type?: string; text?: string }
      }
      if (streamChunk.type === 'finish' && streamChunk.reason?.kind && streamChunk.reason.kind !== 'stop') {
        throw new Error(streamChunk.reason.failure?.message || `DSH 模型调用未完成（${streamChunk.reason.kind}）`)
      }
      // DSH providers may emit either a text-delta or a plain text chunk.
      // Reuse the Agent bridge normalizer so a supported provider cannot
      // complete with retrieved evidence but an empty assistant response.
      const delta = chunkText(chunk)
      if (delta) { text += delta; sse(res, 'message.delta', { delta }) }
      // Some adapters expose only the final assembled text block. It is a
      // fallback for that case, not an additional append after deltas.
      if (!text && streamChunk.type === 'block-end' && streamChunk.block?.type === 'text') {
        text = streamChunk.block.text ?? ''
        if (text) sse(res, 'message.delta', { delta: text })
      }
      if (chunk.type === 'reasoning-delta' && typeof chunk.text === 'string') sse(res, 'reasoning.delta', { delta: chunk.text })
      if (chunk.type === 'usage' && chunk.usage && typeof chunk.usage === 'object') { usage = chunk.usage as Record<string, unknown>; sse(res, 'context.updated', { context: usage }) }
    }
    sse(res, 'run.completed', { content: text, retrieval_hits: evidence.length, citations: [...citationSummary(evidence), ...externalCitationSummary(webSources)], context: usage, route: { mode: options.routeMode ?? 'controlled', retrieval_rounds: 1, web_enabled: Boolean(body.web_enabled), web_hits: webSources.length, degraded: Boolean(degradedReason), degradation_reason: degradedReason } })
  } catch (error) { sse(res, 'run.failed', { error: error instanceof Error ? error.message : String(error) }) } finally { res.end() }
}

async function streamAgentAnswer(ctx: GatewayContext, config: Config, req: IncomingMessage, res: ServerResponse, body: Record<string, unknown>): Promise<void> {
  if (!ctx.agents) throw new Error('DSH agent loop is unavailable')
  const query = asString(body.query, 'query', 4000)
  const provider = asString(body.provider, 'provider', 200)
  const model = asString(body.model, 'model', 300)
  const sourceIds = asStringList(body.source_ids, 'source_ids', 8)
  const delegationToken = asString(body.delegation_token, 'delegation_token', 10_000)
  const sessionId = typeof body.session_id === 'string' && body.session_id.trim() ? body.session_id.trim() : `sag-${Date.now()}`
  const preset = typeof body.permission_preset === 'string' ? body.permission_preset : undefined
  const previous = history(body.history, Math.floor(config.contextTokenBudget * 0.25))
  const imageRefs = await decodeImages(ctx, body.images) as ImageRef[]
  if (preset && ctx.permissionPresets) ctx.permissionPresets.resolve(preset)
  let cancelled = false
  let handle: { agent: GatewayAgent; dispose(): Promise<void> } | undefined
  let retrievalCalls = 0
  let lastEvidence: Evidence[] = []
  let webSources: readonly WebSource[] = []
  let webFailure: string | undefined
  let pump: ReturnType<typeof setInterval> | undefined
  let fallbackHandled = false
  sseStart(res)
  req.once('close', () => { cancelled = true; handle?.agent.cancel({ kind: 'user' }, { keepInbox: true }) })
  try {
    sse(res, 'run.started', { mode: 'agent', session_id: sessionId, source_ids: sourceIds, web_enabled: Boolean(body.web_enabled) })
    sse(res, 'research.started', { max_rounds: config.maxResearchRounds, query })
    let researchQuery = query
    for (let round = 1; round <= config.maxResearchRounds; round += 1) {
      sse(res, 'retrieval.started', { query: researchQuery, source_ids: sourceIds, round })
      const retrieval = await scopedSearch(config, delegationToken, researchQuery, sourceIds, config.defaultTopK)
      lastEvidence = mergeEvidence(lastEvidence, citations(retrieval))
      retrievalCalls = round
      sse(res, 'retrieval.completed', { query: researchQuery, retrieval_hits: lastEvidence.length, citations: citationSummary(lastEvidence), stats: retrieval.stats ?? {}, round: retrievalCalls })
      const sufficiency = evidenceSufficient(lastEvidence, query, round, config.maxResearchRounds)
      sse(res, 'research.progress', { round, evidence_hits: lastEvidence.length, source_count: sufficiency.sources, strong_hits: sufficiency.strong, coverage: Number(sufficiency.coverage.toFixed(3)), sufficient: sufficiency.sufficient })
      if (sufficiency.sufficient) {
        sse(res, 'research.completed', { rounds: round, evidence_hits: lastEvidence.length, coverage: Number(sufficiency.coverage.toFixed(3)), reason: round >= config.maxResearchRounds ? 'max_rounds' : 'evidence_sufficient' })
        break
      }
      const rewritten = await rewriteResearchQuery(ctx, query, round, provider, model)
      researchQuery = rewritten.query
      sse(res, 'research.query_rewritten', { round: round + 1, query: researchQuery, rationale: rewritten.rationale })
    }
    if (body.web_enabled) {
      const webOutcome = await searchWeb(ctx, query, res)
      webSources = webOutcome.sources
      webFailure = webOutcome.error
      if (webFailure) sse(res, 'run.degraded', { from: 'web_search', to: 'knowledge_only', reason: webFailure })
    }
    handle = await ctx.agents.create({
      sessionId,
      meta: { cwd: '/' },
      agentOptions: { provider, model, maxTokens: 4096 },
      setup: (agentCtx: GatewayAgent['ctx']) => {
        const tools = agentCtx.tools
        if (!tools) throw new Error('DSH scoped tools service is unavailable')
        tools.register({
          name: 'sag_gateway_search',
          description: 'Search only the SAG knowledge sources explicitly selected for this conversation. Use it repeatedly when the first evidence set is insufficient. Never use it outside the selected source scope.',
          parameters: { type: 'object', properties: { query: { type: 'string' }, top_k: { type: 'integer' } }, required: ['query'], additionalProperties: false },
          output: { schema: { type: 'object' }, render: (_args: unknown, value: unknown) => [{ type: 'text', text: JSON.stringify(value) }] },
          execute: async (args: { query?: unknown; top_k?: unknown }, execution: { signal?: AbortSignal }) => {
            const rewritten = asString(args.query, 'query', 2000)
            if (retrievalCalls >= config.maxResearchRounds) return { query: rewritten, sections: [], stats: { reason: 'max_research_rounds_reached' } }
            retrievalCalls += 1
            sse(res, 'retrieval.started', { query: rewritten, source_ids: sourceIds, round: retrievalCalls })
            const response = await scopedSearch(config, delegationToken, rewritten, sourceIds, typeof args.top_k === 'number' ? Math.min(Math.max(Math.floor(args.top_k), 1), 20) : config.defaultTopK)
            lastEvidence = mergeEvidence(lastEvidence, citations(response))
            sse(res, 'retrieval.completed', { query: rewritten, retrieval_hits: lastEvidence.length, citations: citationSummary(lastEvidence), stats: response.stats ?? {}, round: retrievalCalls })
            execution.signal?.throwIfAborted()
            return compactRetrieval(response)
          },
        })
        // `sag_gateway_search` is registered in this agent's scope. The online
        // lookup is completed above through ctx.web.search and injected as
        // evidence, rather than delegated to a host-global tool whose name is
        // profile-specific (for example, some DSH installations provide
        // `x_search`, not `web_search`). Keep all global tools hidden while
        // retaining this scoped retrieval tool for follow-up research rounds.
        tools.restrict({ allow: [] })
      },
    })
    if (preset && ctx.permissionPresets) ctx.permissionPresets.set(handle.agent.session, preset)
    let eventCursor = 0
    pump = setInterval(() => {
      const events = handle?.agent.session.events ?? []
      for (const event of events.slice(eventCursor)) {
        eventCursor += 1
        if (event.type === 'assistant/chunk') {
          const delta = chunkText(event.data.chunk ?? event.data)
          if (delta) sse(res, 'message.delta', { delta })
        }
        else if (event.type === 'tool/call' || event.type === 'tool/result') sse(res, 'tool.event', { event: event.type, data: event.data })
      }
    }, 40)
    const priorText = previous.length ? `Conversation history:\n${previous.map(item => `${item.role}: ${item.content}`).join('\n\n')}\n\n` : ''
    const initialEvidence = formatEvidence(lastEvidence, Math.floor(config.contextTokenBudget * 0.65))
    const onlineEvidence = webSources.length ? webEvidenceText(webSources, Math.floor(config.contextTokenBudget * 0.2)) : '(No online sources returned.)'
    handle.agent.followup(createUserMessage({ content: [{ type: 'text', text: `${priorText}Current user request:\n${query}\n\nInitial SAG knowledge evidence:\n${initialEvidence || '(No SAG evidence returned.)'}${body.web_enabled ? `\n\nInitial online evidence:\n${onlineEvidence}\n\nSynthesize both evidence sets. Cite web claims using the exact markdown URLs above, and state when sources conflict or are insufficient.` : ''}` }, ...imageRefs.map(attachment => ({ type: 'image' as const, attachment }))] as never, source: { kind: 'user' } }))
    await Promise.race([
      handle.agent.whenIdle(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error('DSH Agent 响应超时')), 90_000)),
    ])
    clearInterval(pump)
    pump = undefined
    const answer = [...handle.agent.session.events].reverse().map(event => event.type === 'assistant/message' ? messageText(event.data) : '').find(Boolean) ?? ''
    // Some configured DSH adapters acknowledge an Agent turn without driving
    // a model step. Keep the request useful and bounded by falling back to the
    // same provider through controlled RAG instead of returning an empty answer.
    if (!answer) {
      handle.agent.cancel({ kind: 'user' })
      await handle.agent.whenIdle().catch(() => undefined)
      sse(res, 'run.degraded', { from: 'agent', to: 'controlled_rag', reason: 'DSH Agent 未返回可用答案' })
      return await streamControlledAnswer(ctx, config, res, body, { alreadyStarted: true, routeMode: 'agent-fallback', degradedReason: 'DSH Agent 未返回可用答案', req })
    }
    if (cancelled) sse(res, 'run.cancelled', { session_id: sessionId })
    else sse(res, 'run.completed', { content: answer, retrieval_hits: lastEvidence.length, citations: [...citationSummary(lastEvidence), ...externalCitationSummary(webSources)], context: ctx.tokenMeter?.measure(handle.agent.session) ?? {}, route: { mode: 'agent', retrieval_rounds: retrievalCalls, web_enabled: Boolean(body.web_enabled), web_hits: webSources.length, degraded: Boolean(webFailure), degradation_reason: webFailure } })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (!cancelled && message.includes('响应超时')) {
      fallbackHandled = true
      if (pump) { clearInterval(pump); pump = undefined }
      handle?.agent.cancel({ kind: 'user' })
      await handle?.agent.whenIdle().catch(() => undefined)
      sse(res, 'run.degraded', { from: 'agent', to: 'controlled_rag', reason: message })
      await streamControlledAnswer(ctx, config, res, { ...body, web_enabled: false }, { alreadyStarted: true, routeMode: 'agent-timeout-fallback', degradedReason: message, req })
    } else {
      sse(res, 'run.failed', { error: message, session_id: sessionId })
    }
  } finally {
    if (pump) clearInterval(pump)
    await handle?.dispose().catch(() => undefined)
    if (!fallbackHandled) res.end()
  }
}

function mountGateway(ctx: GatewayContext, config: Config, log: (message: string) => void): void {
  const token = gatewayToken(config)
  if (!token) { log('SAG Agent Gateway disabled: configure gatewayToken or DSH_SAG_GATEWAY_TOKEN_FILE'); return }
  if (!ctx.webServer) { log('SAG Agent Gateway unavailable: webServer is not composed'); return }
  const path = config.gatewayPath.replace(/\/$/, '')
  ctx.effect(() => ctx.webServer!.register({ kind: 'prefix', path, handler: async (req, res) => {
    if (!authorized(req, token)) return writeJson(res, 401, { error: 'unauthorized' })
    const pathname = new URL(req.url ?? '/', 'http://localhost').pathname
    if (req.method === 'GET' && pathname === `${path}/health`) return writeJson(res, 200, { ok: true, gateway: 'sag-agent', protocol: 2 })
    if (req.method === 'GET' && pathname === `${path}/capabilities`) { try { return writeJson(res, 200, await modelCatalog(ctx)) } catch (error) { return writeJson(res, 503, { error: String(error) }) } }
    if (req.method !== 'POST' || !req.headers['content-type']?.toLowerCase().startsWith('application/json')) return writeJson(res, 415, { error: 'JSON POST required' })
    try {
      const body = await readJson(req, config.gatewayMaxBodyBytes)
      if (pathname === `${path}/generate`) return await streamControlledAnswer(ctx, config, res, body, { req })
      if (pathname === `${path}/agent`) return await streamAgentAnswer(ctx, config, req, res, body)
      return writeJson(res, 404, { error: 'not found' })
    } catch (error) { return writeJson(res, 400, { error: error instanceof Error ? error.message : String(error) }) }
  } }))
  log(`SAG Agent Gateway mounted at ${path}`)
}

export function apply(ctx: GatewayContext, config: Config): void {
  const log = (message: string): void => { ctx.logger?.info?.(`[dsh-sag-knowledge] ${message}`) }
  try {
    const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
    const raw = readFileSync(join(pkgRoot, 'skills', 'sag-knowledge', 'SKILL.md'), 'utf8')
    const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
    const front = fm?.[1] ?? ''
    ctx.skills.register({ name: /^name:\s*(.+)$/m.exec(front)?.[1]?.trim() ?? 'sag-knowledge', description: /^description:\s*(.+)$/m.exec(front)?.[1]?.trim() ?? 'SAG knowledge retrieval', content: fm ? raw.slice(fm[0].length) : raw, source: 'runtime', provider: name, invocation: { modelInvocable: true, userInvocable: true } })
  } catch (error) { log(`skill registration failed: ${String(error).slice(0, 160)}`) }
  const sag = { token: null as string | null, tokenExpiresAt: 0, async ensureToken(force = false): Promise<string> { const now = Date.now(); if (!force && sag.token && sag.tokenExpiresAt > now + 60_000) return sag.token; const body = await sagRequest(config.sagApiBase, '/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ name: config.loginName }) }) as { access_token?: string }; if (!body.access_token) throw new Error('SAG login response has no access_token'); sag.token = body.access_token; sag.tokenExpiresAt = now + 7 * 86400_000; return sag.token }, async get(path: string): Promise<unknown> { try { return await sagRequest(config.sagApiBase, path, { headers: { Authorization: `Bearer ${await sag.ensureToken()}` } }) } catch (error) { if (!(error instanceof UnauthorizedError)) throw error; return sagRequest(config.sagApiBase, path, { headers: { Authorization: `Bearer ${await sag.ensureToken(true)}` } }) } }, async post(path: string, payload: unknown): Promise<unknown> { const request = async (force = false) => sagRequest(config.sagApiBase, path, { method: 'POST', body: JSON.stringify(payload), headers: { Authorization: `Bearer ${await sag.ensureToken(force)}` } }); try { return await request() } catch (error) { if (!(error instanceof UnauthorizedError)) throw error; return request(true) } } }
  ctx.tools.register({ name: 'sag_list_sources', description: '列出 SAG 知识库中的所有信源（知识库）及其文档/分块统计。', parameters: {}, output: { schema: {}, render: (_args: unknown, value: unknown) => [{ type: 'text', text: JSON.stringify(value) }] }, execute: async () => { const data = await sag.get('/api/v1/sources') as Array<{ id: string; name: string; document_count?: number; chunk_count?: number }>; return { sources: data.map(source => ({ id: source.id, name: source.name, documents: source.document_count ?? 0, chunks: source.chunk_count ?? 0 })) } } })
  ctx.tools.register({ name: 'sag_search', description: '在 SAG 知识库中检索资料证据。可传 source_ids 限定范围。', parameters: { query: { type: 'string', required: true }, source_ids: { type: 'array', items: { type: 'string' } }, top_k: { type: 'integer' }, strategy: { type: 'string', enum: ['vector', 'multi'] } }, output: { schema: {}, render: (_args: unknown, value: unknown) => [{ type: 'text', text: JSON.stringify(value) }] }, execute: async (args: { query: string; source_ids?: string[]; top_k?: number; strategy?: string }) => { const body = await sag.post('/api/v1/search', { query: args.query, top_k: Math.min(Math.max(args.top_k ?? config.defaultTopK, 1), 20), strategy: args.strategy === 'vector' ? 'vector' : 'multi', ...(args.source_ids?.length ? { source_ids: args.source_ids } : {}) }) as { sections?: Array<Record<string, unknown>>; source_hits?: unknown[] }; return { query: args.query, evidence: body.sections ?? [], source_hits: body.source_hits ?? [] } } })
  mountGateway(ctx, config, log)
  if (config.autoLogin) void sag.ensureToken().catch(error => log(`startup login failed: ${String(error).slice(0, 120)}`))
}
