import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import { sagPanel } from './controller.ts'
import type {
  ClientSessionContext,
  InputTriggerPick,
  InputTriggerServiceContract,
  InputTriggerSource,
} from '@deepseek-ai/dsh-client-ui-input-trigger/client'

interface SagSource {
  id: string
  name: string
  document_count?: number
  chunk_count?: number
}

interface MentionValue {
  id: string
  name: string
  documents: number
  chunks: number
}

const cache = new Map<string, { values: SagSource[]; expiresAt: number }>()

function apiBase(): string {
  return `http://${window.location.hostname}:8000`
}

async function sources(signal: AbortSignal): Promise<SagSource[]> {
  const key = window.location.host
  const hit = cache.get(key)
  if (hit && hit.expiresAt > Date.now()) return hit.values

  const login = await fetch(`${apiBase()}/api/v1/auth/login`, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'dsh-sag' }),
  })
  if (!login.ok) return []
  const token = (await login.json() as { access_token?: string }).access_token
  if (!token) return []
  const response = await fetch(`${apiBase()}/api/v1/sources`, {
    signal,
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) return []
  const values = await response.json() as SagSource[]
  cache.set(key, { values, expiresAt: Date.now() + 60_000 })
  return values
}

function valueOf(raw: string | undefined): MentionValue | undefined {
  if (!raw) return undefined
  try { return JSON.parse(raw) as MentionValue } catch { return undefined }
}

export function registerSagSourceMention(ctx: ClientContext): void {
  const source: InputTriggerSource = {
    trigger: '@',
    name: 'sag-knowledge',
    order: -20,
    showGroupTitle: true,
    async candidates(_session: ClientSessionContext, { query, signal }) {
      const needle = query.trim().toLocaleLowerCase()
      const values = await sources(signal)
      if (signal.aborted) return []
      return values
        .filter(value => !needle || value.name.toLocaleLowerCase().includes(needle))
        .map(value => ({
          name: value.name,
          description: `${value.document_count ?? 0} 文档 · ${value.chunk_count ?? 0} 分块`,
          section: 'SAG 知识库',
          value: JSON.stringify({
            id: value.id,
            name: value.name,
            documents: value.document_count ?? 0,
            chunks: value.chunk_count ?? 0,
          } satisfies MentionValue),
        }))
    },
    onPick({ candidate }: InputTriggerPick) {
      const value = valueOf(candidate.value)
      if (!value) return undefined
      // Keep a stable source-id bridge for the separate native SAG Agent tab.
      sagPanel.addAgentSourceId(value.id)
      return {
        insert: {
          source: 'sag-knowledge',
          ref: `sag-source:${value.id}`,
          label: `@${value.name}`,
          appearance: 'folder',
          clipboardText: `@${value.name}`,
        },
      }
    },
    codec: {
      clipboardText(ref: string) {
        const id = ref.slice('sag-source:'.length)
        const values = cache.get(window.location.host)?.values ?? []
        return `@${values.find(value => value.id === id)?.name ?? id}`
      },
      async serialize(ref: string) {
        const id = ref.slice('sag-source:'.length)
        const values = await sources(new AbortController().signal)
        const value = values.find(item => item.id === id)
        return `<sag-source id="${id}" name="${value?.name ?? id}" />`
      },
    },
    lexicon() {
      return cache.get(window.location.host)?.values.map(value => value.name)
    },
  }
  const triggers = ctx.get('inputTriggers') as InputTriggerServiceContract
  ctx.effect(() => triggers.registerSource(source), 'sag-knowledge: @ source')
}
