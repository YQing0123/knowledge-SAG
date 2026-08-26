import type { Context } from 'cordis'
import z from 'schemastery'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

type AppContext = Context & {
  tools: { register(definition: unknown): void }
  skills: { register(skill: Record<string, unknown>): () => void }
}

export const name = '@dsh-external/dsh-sag-knowledge'
export const inject = ['tools', 'skills']

export interface Config {
  sagApiBase: string
  sagWebBase: string
  loginName: string
  autoLogin: boolean
  defaultTopK: number
}

export const Config = z.object({
  sagApiBase: z.string().default('http://127.0.0.1:8000'),
  sagWebBase: z.string().default('http://127.0.0.1:3000'),
  loginName: z.string().default('dsh-sag'),
  autoLogin: z.boolean().default(true),
  defaultTopK: z.number().min(1).max(50).default(8),
})

class UnauthorizedError extends Error {
  constructor() { super('SAG token expired'); this.name = 'UnauthorizedError' }
}

async function sagRequest(base: string, path: string, init: RequestInit = {}): Promise<unknown> {
  const res = await fetch(base + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`SAG ${init.method ?? 'GET'} ${path} -> ${res.status} ${body.slice(0, 200)}`)
  }
  return res.json() as Promise<unknown>
}

export function apply(ctx: AppContext, config: Config): void {
  const log = (msg: string): void => { ctx.logger?.info?.(`[dsh-sag-knowledge] ${msg}`) }

  try {
    const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
    const raw = readFileSync(join(pkgRoot, 'skills', 'sag-knowledge', 'SKILL.md'), 'utf8')
    const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
    const front = fm?.[1] ?? ''
    const content = fm ? raw.slice(fm[0].length) : raw
    const skillName = /^name:\s*(.+)$/m.exec(front)?.[1]?.trim() ?? 'sag-knowledge'
    const description = /^description:\s*(.+)$/m.exec(front)?.[1]?.trim() ?? 'SAG knowledge retrieval: /sag + @ sources'
    ctx.skills.register({
      name: skillName,
      description,
      content,
      source: 'runtime',
      provider: name,
      invocation: { modelInvocable: true, userInvocable: true },
    })
    log(`skill registered: ${skillName}`)
  } catch (error) {
    log(`skill registration failed (tools remain available): ${String(error).slice(0, 160)}`)
  }

  const sag = {
    token: null as string | null,
    tokenExpiresAt: 0,
    async ensureToken(force = false): Promise<string> {
      const now = Date.now()
      if (!force && sag.token && sag.tokenExpiresAt > now + 60_000) return sag.token
      const body = await sagRequest(config.sagApiBase, '/api/v1/auth/login', {
        method: 'POST', body: JSON.stringify({ name: config.loginName }),
      }) as { access_token?: string }
      if (!body.access_token) throw new Error('SAG login response has no access_token')
      sag.token = body.access_token
      sag.tokenExpiresAt = now + 7 * 24 * 3600 * 1000
      return sag.token
    },
    async get(path: string): Promise<unknown> {
      try {
        return await sagRequest(config.sagApiBase, path, { headers: { Authorization: `Bearer ${await sag.ensureToken()}` } })
      } catch (error) {
        if (!(error instanceof UnauthorizedError)) throw error
        return sagRequest(config.sagApiBase, path, { headers: { Authorization: `Bearer ${await sag.ensureToken(true)}` } })
      }
    },
    async post(path: string, payload: unknown): Promise<unknown> {
      const request = async (force = false) => sagRequest(config.sagApiBase, path, {
        method: 'POST', body: JSON.stringify(payload), headers: { Authorization: `Bearer ${await sag.ensureToken(force)}` },
      })
      try { return await request() } catch (error) {
        if (!(error instanceof UnauthorizedError)) throw error
        return request(true)
      }
    },
  }

  ctx.tools.register({
    name: 'sag_list_sources',
    description: '列出 SAG 知识库中的所有信源（知识库）及其文档/分块统计。在用户用 @ 指定一个或多个知识库前必须先调用本工具解析知识库名 → id；也可用于了解当前库内有什么。',
    parameters: {},
    output: { schema: {}, render: (_args: unknown, value: unknown) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: async () => {
      const data = await sag.get('/api/v1/sources') as Array<{ id: string; name: string; document_count?: number; chunk_count?: number }>
      if (!Array.isArray(data) || data.length === 0) return { sources: [], note: 'SAG 知识库还没有信源，请先在 SAG 面板上传文档。' }
      return { sources: data.map(s => ({ id: s.id, name: s.name, documents: s.document_count ?? 0, chunks: s.chunk_count ?? 0 })) }
    },
  })

  ctx.tools.register({
    name: 'sag_search',
    description: '在 SAG 知识库中检索资料证据（SAG 检索引擎：事件-实体索引 + 查询时动态超边）。回答依赖知识库事实时必须调用。返回按相关度排序的证据分块（含来源信源名、标题、原文内容、得分）。可传 source_ids 限定到用户 @ 选择的若干知识库；可多轮调用，每次用不同角度改写 query。',
    parameters: {
      query: { type: 'string', required: true, description: '要检索的问题或关键词' },
      source_ids: { type: 'array', items: { type: 'string' }, description: '限定检索范围的知识库 id 列表' },
      top_k: { type: 'integer' },
      strategy: { type: 'string', enum: ['vector', 'multi'], description: '默认 multi；快速可用 vector' },
    },
    output: { schema: {}, render: (_args: unknown, value: unknown) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: async (args: { query: string; source_ids?: string[]; top_k?: number; strategy?: string }) => {
      const strategy = args.strategy === 'vector' ? 'vector' : 'multi'
      const body = await sag.post('/api/v1/search', {
        query: args.query,
        top_k: Math.min(Math.max(args.top_k ?? config.defaultTopK, 1), 50),
        strategy,
        ...(args.source_ids?.length ? { source_ids: args.source_ids } : {}),
      }) as { sections?: Array<{ chunk_id?: string | null; heading?: string; content?: string; score?: number; rank?: number; source_id?: string | null; source_name?: string | null }>; source_hits?: unknown[] }
      const sections = body.sections ?? []
      return {
        query: args.query, strategy, source_scope: args.source_ids?.length ? args.source_ids : 'all',
        evidence: sections.map((s, i) => ({ rank: s.rank ?? i + 1, source_id: s.source_id, source_name: s.source_name, heading: s.heading, score: s.score, content: s.content })),
        source_hits: body.source_hits ?? [],
        note: sections.length === 0 ? '未检索到相关证据。' : `检索到 ${sections.length} 条证据，回答时请标注来源。`,
      }
    },
  })

  if (config.autoLogin) void sag.ensureToken().catch(error => log(`startup login failed: ${String(error).slice(0, 120)}`))
  log(`mounted: API=${config.sagApiBase} Web=${config.sagWebBase}`)
}

export default { name, inject, Config, apply }
