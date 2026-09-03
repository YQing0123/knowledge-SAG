"use client";

import * as React from "react";
import {
  AtSign,
  Bot,
  BrainCircuit,
  Check,
  Command,
  Copy,
  Database,
  Globe2,
  ImagePlus,
  Library,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  Trash2,
  WandSparkles,
  X,
} from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { AuthImage } from "@/components/features/auth-image";
import {
  ConversationTranscript,
  type ConversationMessageRenderContext,
  type ConversationTranscriptMessage,
} from "@/components/features/chat/conversation-transcript";
import type { AgentActivityMatch, AgentActivityStep } from "@/components/features/chat/agent-activity-timeline";
import { CitationBlock } from "@/components/features/chat/citation-block";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { isComposerCompositionKeyEvent } from "@/lib/composer-keyboard";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { api, type DshCapabilities, type DshMessageRecord, type DshSessionRecord } from "@/lib/api";
import type { Citation, Source } from "@/lib/types";
import { cn } from "@/lib/utils";
import { copyText } from "@/lib/clipboard";
import { useDetailPanel } from "@/components/features/detail-panel";
import { useApp } from "@/components/features/app-shell";

type GatewayCitation = {
  kind?: "internal" | "external";
  source_id?: string;
  source_name?: string;
  document_name?: string;
  chunk_id?: string;
  heading?: string;
  score?: number;
  url?: string;
  title?: string;
  summary?: string;
  source?: string;
};

type GatewayEvent = {
  type?: string;
  delta?: string;
  content?: string;
  error?: string;
  reason?: string;
  query?: string;
  round?: number;
  retrieval_hits?: number;
  web_hits?: number;
  citations?: GatewayCitation[];
  context?: Record<string, unknown>;
  stats?: Record<string, unknown>;
  route?: Record<string, unknown>;
  rationale?: string;
  coverage?: number;
  source_count?: number;
  strong_hits?: number;
  max_rounds?: number;
  [key: string]: unknown;
};

type DraftImage = { file: File; url: string };
type LiveTurn = {
  content: string;
  events: GatewayEvent[];
  citations: GatewayCitation[];
  context: Record<string, unknown>;
  route: Record<string, unknown>;
  retrievalHits: number;
  webHits: number;
  error?: string;
};

type DshTranscriptMessage = DshMessageRecord & ConversationTranscriptMessage & {
  citations: Citation[];
  steps: AgentActivityStep[];
};

const EMPTY_LIVE: LiveTurn = {
  content: "",
  events: [],
  citations: [],
  context: {},
  route: {},
  retrievalHits: 0,
  webHits: 0,
};

const COMMANDS = [
  { command: "/rag", title: "普通 RAG", hint: "一次检索后基于证据回答", action: "rag" },
  { command: "/deep", title: "深度研究", hint: "允许 DSH 多轮改写查询与检索", action: "deep" },
  { command: "/web", title: "联网搜索", hint: "启用 DSH 已配置的联网工具", action: "web" },
  { command: "/clear", title: "清空输入", hint: "清空当前草稿与图片", action: "clear" },
] as const;

function readNumber(value: Record<string, unknown>): number | null {
  for (const key of ["totalTokens", "total_tokens", "total", "inputTokens", "input_tokens"]) {
    if (typeof value[key] === "number" && Number.isFinite(value[key])) return value[key] as number;
  }
  return null;
}

function titleForSession(session: DshSessionRecord, sources: Source[] = []) {
  return sessionTitleFromQuery(session.title, selectedSources(sources, session.source_ids ?? []));
}

function sessionTitleFromQuery(query: string, selected: Source[]) {
  let title = query.replace(/[\n\r]+/g, " ");
  for (const source of selected) {
    title = title.replace(new RegExp(`@${source.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "giu"), " ");
  }
  // Also remove a dangling @mention when a source was deleted or renamed.
  return title.replace(/(^|\s)@[\S]+/gu, "$1").replace(/\s+/g, " ").trim().slice(0, 80) || "DSH 知识会话";
}

function selectedSources(sources: Source[], ids: string[]) {
  const selected = new Set(ids);
  return sources.filter((source) => selected.has(source.id));
}

function displayDocumentName(value: string | undefined) {
  return value?.replace(/\.(?:md|markdown)$/iu, "") ?? "";
}

function asCitations(items: GatewayCitation[]): Citation[] {
  const seen = new Set<string>();
  const unique = items.flatMap((item) => {
    const key = item.kind === "external"
      ? `external:${item.url ?? ""}`
      : `internal:${item.source_id ?? ""}:${item.chunk_id ?? ""}:${item.heading ?? ""}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [item];
  });
  return unique.map((item, index) => ({
      n: index + 1,
      kind: item.kind === "external" ? "external" : "internal",
      chunk_id: item.chunk_id ?? null,
      heading: item.heading ?? "",
      snippet: "",
      score: item.score ?? 0,
      source_id: item.source_id ?? null,
      source_name: item.source_name ?? null,
      url: item.url ?? null,
      title: item.kind === "external"
        ? (item.title ?? item.source_name ?? item.heading ?? null)
        : ([displayDocumentName(item.document_name) || item.heading, item.source_name].filter((value, index, values) => Boolean(value) && values.indexOf(value) === index).join(" - ") || item.source_name || item.heading || null),
      summary: item.summary,
      source: item.source,
    }));
}

function UserAttachments({ attachments }: { attachments: DshMessageRecord["attachments"] }) {
  if (!attachments.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {attachments.map((attachment) => (
        <AuthImage
          key={attachment.id}
          url={api.attachmentUrl(attachment.id)}
          alt={attachment.name || "会话图片"}
          className="size-20 rounded-md border object-cover"
        />
      ))}
    </div>
  );
}

function eventSteps(events: Array<Record<string, unknown>>, active: boolean): AgentActivityStep[] {
  const steps: AgentActivityStep[] = [];
  const toolSteps = new Map<string, number>();
  const elapsed = (event: Record<string, unknown>) => typeof event.elapsed_ms === "number" ? event.elapsed_ms : undefined;
  for (const [index, event] of events.entries()) {
    const type = String(event.type ?? "");
    const round = typeof event.round === "number" ? event.round : index + 1;
    if (type === "research.started" || type === "research.progress" || type === "research.query_rewritten" || type === "research.completed") {
      const label = type === "research.query_rewritten"
        ? `改写第 ${round} 轮检索 query`
        : type === "research.completed" ? "深度研究证据已收敛" : "分析问题与证据覆盖范围";
      steps.push({ id: `research-${index}`, kind: "thinking", step: round, label, args: typeof event.rationale === "string" ? event.rationale : undefined, ms: elapsed(event), status: type === "research.completed" ? "done" : active ? "active" : "done" });
      continue;
    }
    if (type === "retrieval.started" || type === "retrieval.completed") {
      const key = `retrieval-${round}`;
      const matches = Array.isArray(event.citations) ? asCitations(event.citations as GatewayCitation[]).map((citation) => ({ n: citation.n, chunk_id: citation.chunk_id, source_id: citation.source_id, source_name: citation.source_name ?? undefined, heading: citation.heading, snippet: citation.snippet, score: citation.score })) : [];
      const step: AgentActivityStep = { id: key, kind: "tool", step: round, name: "search_context", label: `知识库检索（第 ${round} 轮）`, arguments: typeof event.query === "string" ? { query: event.query } : undefined, details: { scope: "knowledge", matches, sources: matches.flatMap((match) => match.source_id && match.source_name ? [{ id: match.source_id, name: match.source_name }] : []) }, count: typeof event.retrieval_hits === "number" ? event.retrieval_hits : undefined, ms: elapsed(event), status: type === "retrieval.completed" ? "done" : active ? "active" : "done" };
      const existing = toolSteps.get(key);
      if (existing === undefined) { toolSteps.set(key, steps.length); steps.push(step); }
      else steps[existing] = { ...steps[existing], ...step, details: step.details ?? steps[existing].details };
      continue;
    }
    if (type === "web_search.started" || type === "web_search.completed" || type === "web_search.failed") {
      const key = "web-search";
      const step: AgentActivityStep = { id: key, kind: "tool", step: round, name: "web_search", label: "联网搜索", arguments: typeof event.query === "string" ? { query: event.query } : undefined, count: typeof event.web_hits === "number" ? event.web_hits : undefined, ms: elapsed(event), error: type === "web_search.failed" && typeof event.error === "string" ? event.error : undefined, status: type === "web_search.failed" ? "error" : type === "web_search.completed" ? "done" : active ? "active" : "done", details: { scope: "internet" } };
      const existing = toolSteps.get(key);
      if (existing === undefined) { toolSteps.set(key, steps.length); steps.push(step); }
      else steps[existing] = { ...steps[existing], ...step };
      continue;
    }
    if (type === "message.delta") steps.push({ id: "answer", kind: "answer", step: round, label: "正在整理回答", status: active ? "active" : "done" });
    if (type === "run.degraded") steps.push({ id: `degraded-${index}`, kind: "thinking", step: round, label: typeof event.to === "string" ? `已降级到 ${event.to}` : "已执行降级策略", args: typeof event.reason === "string" ? event.reason : undefined, status: "done" });
  }
  return steps;
}

function DshMessageActions({ message, citations, onSave, onRetry, onCitationClick }: { message: DshTranscriptMessage; citations: Citation[]; onSave: () => void; onRetry?: () => void; onCitationClick: (citation: Citation) => void }) {
  const [copied, setCopied] = React.useState(false);
  const buttonClass = "grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground";
  return (
    <div className="mt-1 flex min-h-7 flex-wrap items-center gap-x-1 gap-y-0">
      <CitationBlock citations={citations} onCitationClick={onCitationClick} />
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover/msg:opacity-100 focus-within:opacity-100">
        <Tooltip><TooltipTrigger asChild><button type="button" onClick={onSave} className={buttonClass} aria-label="回答入库"><Library className="size-3.5" /></button></TooltipTrigger><TooltipContent>入库为 Markdown</TooltipContent></Tooltip>
        <Tooltip><TooltipTrigger asChild><button type="button" onClick={async () => { try { await copyText(message.content); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { toast.error("复制失败，请检查浏览器权限"); } }} className={buttonClass} aria-label={copied ? "已复制" : "复制回答"}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</button></TooltipTrigger><TooltipContent>{copied ? "已复制" : "复制"}</TooltipContent></Tooltip>
        {onRetry ? <Tooltip><TooltipTrigger asChild><button type="button" onClick={onRetry} className={buttonClass} aria-label="重新回答"><RotateCcw className="size-3.5" /></button></TooltipTrigger><TooltipContent>重新回答</TooltipContent></Tooltip> : null}
      </div>
    </div>
  );
}

export function DshSessionWorkspace() {
  const { capabilities: sagCapabilities } = useApp();
  const [sources, setSources] = React.useState<Source[]>([]);
  const [capabilities, setCapabilities] = React.useState<DshCapabilities | null>(null);
  const [sessions, setSessions] = React.useState<DshSessionRecord[]>([]);
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<DshMessageRecord[]>([]);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [query, setQuery] = React.useState("");
  const [images, setImages] = React.useState<DraftImage[]>([]);
  const [live, setLive] = React.useState<LiveTurn>(EMPTY_LIVE);
  const [busy, setBusy] = React.useState(false);
  const [mode, setMode] = React.useState<"controlled" | "agent">("controlled");
  const [webEnabled, setWebEnabled] = React.useState(false);
  const [provider, setProvider] = React.useState("");
  const [model, setModel] = React.useState("");
  const [reasoning, setReasoning] = React.useState("");
  const [permission, setPermission] = React.useState("workspace-write");
  const [mentionOpen, setMentionOpen] = React.useState(false);
  const [commandOpen, setCommandOpen] = React.useState(false);
  const [stepsCollapsed, setStepsCollapsed] = React.useState(true);
  const router = useRouter();
  const detailPanel = useDetailPanel();
  const params = useParams<{ id?: string[] }>();
  const searchParams = useSearchParams();
  const routeSessionId = Array.isArray(params.id) ? params.id[0] : undefined;
  const newRequested = searchParams.get("new") === "1";
  const abortRef = React.useRef<AbortController | null>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null);
  const transcriptRef = React.useRef<HTMLDivElement | null>(null);
  const composingRef = React.useRef(false);
  const compositionCommitGuardRef = React.useRef(false);
  const compositionCommitGuardTimerRef = React.useRef<number | null>(null);
  const restoreComposerFocusRef = React.useRef(false);
  const newHandledRef = React.useRef(false);

  React.useEffect(() => {
    if (sagCapabilities && !sagCapabilities.dsh_enabled) router.replace("/chat");
  }, [router, sagCapabilities]);

  const currentSession = sessions.find((session) => session.id === sessionId) ?? null;
  const lastAssistant = messages.filter((message) => message.role === "assistant").at(-1);
  const runContext = live.events.length ? live.context : (lastAssistant?.context ?? {});
  const runRoute = live.events.length ? live.route : (runContext.route as Record<string, unknown> | undefined ?? {});
  const models = capabilities?.groups.find((group) => group.id === provider)?.models ?? [];
  const currentModel = models.find((item) => item.id === model);
  const contextWindow = currentModel?.context_window ?? 128_000;
  const contextTokens = readNumber(runContext) ?? 0;
  const contextPercent = Math.min(100, (contextTokens / contextWindow) * 100);
  const mentionedSourceIds = React.useMemo(
    () => sources.filter((source) => query.includes(`@${source.name}`)).map((source) => source.id),
    [query, sources],
  );
  const effectiveSourceIds = React.useMemo(
    () => Array.from(new Set([...selected, ...mentionedSourceIds])),
    [mentionedSourceIds, selected],
  );
  const selectedSourceRows = selectedSources(sources, effectiveSourceIds);
  const openDshCitation = React.useCallback((citation: Citation) => {
    if (!citation.chunk_id || !citation.source_id) return;
    detailPanel.open({
      kind: "chunk",
      sourceId: citation.source_id,
      chunkId: citation.chunk_id,
      heading: citation.heading || undefined,
      sourceName: citation.source_name ?? undefined,
      eventRefs: citation.event_refs,
    });
  }, [detailPanel]);
  const transcriptMessages = React.useMemo<DshTranscriptMessage[]>(() => messages.map((message) => ({
    ...message,
    citations: asCitations(Array.isArray(message.context.citations) ? message.context.citations as GatewayCitation[] : []),
    steps: message.role === "assistant" ? eventSteps(message.event_summary, false) : [],
  })), [messages]);
  const liveTranscript = React.useMemo<DshTranscriptMessage | null>(() => !busy ? null : ({
    id: "dsh-live-turn",
    session_id: sessionId ?? "",
    role: "assistant",
    content: live.content,
    attachments: [],
    retrieval_hits: live.retrievalHits,
    context: {},
    event_summary: [],
    created_at: new Date().toISOString(),
    citations: asCitations(live.citations),
    steps: eventSteps(live.events, true),
  }), [busy, live, sessionId]);
  const displayTranscriptMessages = React.useMemo(
    () => liveTranscript ? [...transcriptMessages, liveTranscript] : transcriptMessages,
    [liveTranscript, transcriptMessages],
  );
  const mentionNeedle = /(?:^|\s)@([^\s@]*)$/.exec(query)?.[1]?.toLocaleLowerCase() ?? "";
  const mentionCandidates = sources.filter((source) => !selected.includes(source.id) && (!mentionNeedle || source.name.toLocaleLowerCase().includes(mentionNeedle))).slice(0, 6);
  const commandCandidates = COMMANDS.filter((item) => query.trim().startsWith("/") && item.command.startsWith(query.trim().toLocaleLowerCase())).slice(0, 6);

  const selectSession = React.useCallback(async (next: DshSessionRecord) => {
    setSessionId(next.id);
    setSelected(next.source_ids ?? []);
    setMode(next.mode);
    setWebEnabled(next.web_enabled);
    setProvider(next.provider);
    setModel(next.model);
    setPermission(next.permission_preset);
    setReasoning("");
    setLive(EMPTY_LIVE);
    const history = await api.dshListMessages(next.id);
    setMessages(history);
  }, []);

  const createSession = React.useCallback(async () => {
    // Reuse the most recently-created untouched draft. This keeps repeated
    // clicks on the global DSH entry from filling the history with empty rows.
    const draft = sessions.find((item) => item.title === "新 DSH 会话");
    if (draft) {
      try {
        const history = await api.dshListMessages(draft.id);
        if (history.length === 0) {
          await selectSession(draft);
          router.replace(`/dsh/${draft.id}`);
          textareaRef.current?.focus();
          return;
        }
      } catch {
        // A stale draft must never prevent the user from starting a session.
      }
    }
    const created = await api.dshCreateSession({
      title: "新 DSH 会话",
      source_ids: [],
      provider,
      model,
      permission_preset: permission,
      mode: "controlled",
      web_enabled: false,
    });
    setSessions((current) => [created, ...current]);
    await selectSession(created);
    router.replace(`/dsh/${created.id}`);
    window.dispatchEvent(new Event("sag:dsh-sessions-changed"));
    textareaRef.current?.focus();
  }, [model, permission, provider, router, selectSession, sessions]);

  React.useEffect(() => {
    void Promise.all([api.listSources(), api.dshListSessions(), api.dshCapabilities()])
      .then(async ([nextSources, nextSessions, catalog]) => {
        setSources(nextSources);
        setCapabilities(catalog);
        const firstGroup = catalog.groups[0];
        const firstModel = firstGroup?.models[0];
        if (firstGroup && firstModel) { setProvider(firstGroup.id); setModel(firstModel.id); }
        if (catalog.permission_presets[0]) setPermission(catalog.permission_presets[0]);
        if (nextSessions[0]) {
          setSessions(nextSessions);
          await selectSession(nextSessions.find((item) => item.id === routeSessionId) ?? nextSessions[0]);
        }
        else {
          const created = await api.dshCreateSession({ title: "新 DSH 会话", source_ids: [], provider: firstGroup?.id ?? "", model: firstModel?.id ?? "", permission_preset: catalog.permission_presets[0] ?? "workspace-write", mode: "controlled", web_enabled: false });
          setSessions([created]);
          await selectSession(created);
          // 首次访问且没有历史会话时，这一条就是 ?new=1 所要求的新会话，
          // 不再由下面的 effect 额外创建一条空会话。
          if (newRequested) newHandledRef.current = true;
        }
      })
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : "DSH 会话初始化失败");
      });
  }, [newRequested, routeSessionId, selectSession]);

  React.useEffect(() => {
    if (!newRequested) {
      newHandledRef.current = false;
      return;
    }
    if (!capabilities || !sessions.length || newHandledRef.current) return;
    newHandledRef.current = true;
    void createSession().catch((error) => {
      newHandledRef.current = false;
      toast.error(error instanceof Error ? error.message : "新建 DSH 会话失败");
    });
  }, [capabilities, createSession, newRequested, router, sessions.length]);

  React.useEffect(() => {
    if (!routeSessionId || routeSessionId === sessionId) return;
    const requested = sessions.find((session) => session.id === routeSessionId);
    if (requested) void selectSession(requested);
  }, [routeSessionId, selectSession, sessionId, sessions]);

  React.useEffect(() => {
    const create = () => void createSession().catch((error) => toast.error(error instanceof Error ? error.message : "新建 DSH 会话失败"));
    window.addEventListener("sag:new-dsh", create);
    return () => window.removeEventListener("sag:new-dsh", create);
  }, [createSession]);

  React.useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: busy ? "auto" : "smooth" });
  }, [busy, live.events.length, messages.length]);

  const imagesRef = React.useRef<DraftImage[]>([]);
  React.useEffect(() => { imagesRef.current = images; }, [images]);
  React.useEffect(() => () => {
    abortRef.current?.abort();
    imagesRef.current.forEach((image) => URL.revokeObjectURL(image.url));
    if (compositionCommitGuardTimerRef.current) window.clearTimeout(compositionCommitGuardTimerRef.current);
  }, []);

  function applySessionConfig(patch: Partial<DshSessionRecord>) {
    if (!sessionId) return;
    setSessions((current) => current.map((item) => item.id === sessionId ? { ...item, ...patch } : item));
    void api.dshPatchSession(sessionId, patch).catch(() => toast.error("会话设置保存失败"));
  }

  function toggleSource(source: Source) {
    setSelected((current) => {
      const next = current.includes(source.id) ? current.filter((id) => id !== source.id) : [...current, source.id];
      applySessionConfig({ source_ids: next });
      return next;
    });
  }

  function addImages(files: FileList | File[] | null) {
    if (!files) return;
    const accepted = Array.from(files).filter((file) => file.type.startsWith("image/") && file.size <= 10 * 1024 * 1024);
    if (accepted.length !== Array.from(files).length) toast.error("仅支持 10MB 以内的图片");
    setImages((current) => {
      const room = Math.max(0, 4 - current.length);
      if (accepted.length > room) toast.error("每条消息最多添加 4 张图片");
      return [...current, ...accepted.slice(0, room).map((file) => ({ file, url: URL.createObjectURL(file) }))];
    });
  }

  function removeImage(url: string) {
    setImages((current) => {
      URL.revokeObjectURL(url);
      return current.filter((image) => image.url !== url);
    });
  }

  function chooseMention(source: Source) {
    const replacement = `@${source.name} `;
    setQuery((current) => current.replace(/(?:^|\s)@[^\s@]*$/, (match) => `${match.startsWith(" ") ? " " : ""}${replacement}`));
    setMentionOpen(false);
    if (!selected.includes(source.id)) toggleSource(source);
    textareaRef.current?.focus();
  }

  function chooseCommand(action: (typeof COMMANDS)[number]["action"]) {
    if (action === "rag") { setMode("controlled"); applySessionConfig({ mode: "controlled" }); }
    if (action === "deep") { setMode("agent"); applySessionConfig({ mode: "agent" }); }
    if (action === "web") { setWebEnabled((value) => { const next = !value; applySessionConfig({ web_enabled: next }); return next; }); }
    if (action === "clear") { setQuery(""); images.forEach((image) => URL.revokeObjectURL(image.url)); setImages([]); }
    if (action !== "clear") setQuery("");
    setCommandOpen(false);
    textareaRef.current?.focus();
  }

  function startComposition() {
    composingRef.current = true;
    compositionCommitGuardRef.current = false;
    if (compositionCommitGuardTimerRef.current) window.clearTimeout(compositionCommitGuardTimerRef.current);
  }

  function endComposition() {
    composingRef.current = false;
    compositionCommitGuardRef.current = true;
    if (compositionCommitGuardTimerRef.current) window.clearTimeout(compositionCommitGuardTimerRef.current);
    compositionCommitGuardTimerRef.current = window.setTimeout(() => {
      compositionCommitGuardRef.current = false;
      compositionCommitGuardTimerRef.current = null;
    }, 30);
  }

  function isComposingKeyEvent(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    return isComposerCompositionKeyEvent(event, {
      composing: composingRef.current,
      commitGuard: compositionCommitGuardRef.current,
    });
  }

  const handleToolMatchClick = React.useCallback((match: AgentActivityMatch) => {
    if (!match.chunk_id || !match.source_id) return;
    detailPanel.open({ kind: "chunk", sourceId: match.source_id, chunkId: match.chunk_id, heading: match.heading, sourceName: match.source_name });
  }, [detailPanel]);

  async function consumeStream(response: Response) {
    if (!response.body) throw new Error("DSH 未返回流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let output = "";
    let state = EMPTY_LIVE;
    let terminalType: "run.completed" | "run.failed" | "run.cancelled" | null = null;
    let protocolError: string | null = null;
    const applyEvent = (payload: GatewayEvent) => {
      if (terminalType) {
        protocolError = "DSH 在终态后仍返回了事件";
        return;
      }
      // DSH may emit one token per event. Render the accumulating Markdown as
      // native SAG does, while the activity timeline remains visible above it.
      if (payload.type === "message.delta") {
        if (payload.delta) output += payload.delta;
        state = {
          ...state,
          content: output,
          events: state.events.some((item) => item.type === "message.delta")
            ? state.events
            : [...state.events, { type: "message.delta" }],
        };
        setLive(state);
        return;
      }
      if (payload.type === "reasoning.delta") return;
      if (payload.type === "run.completed" || payload.type === "run.failed" || payload.type === "run.cancelled") terminalType = payload.type;
      state = {
        content: typeof payload.content === "string" ? output = payload.content : output,
        events: [...state.events, payload].slice(-200),
        citations: payload.citations?.length ? payload.citations : state.citations,
        context: payload.context ?? state.context,
        route: payload.route ?? state.route,
        retrievalHits: typeof payload.retrieval_hits === "number" ? payload.retrieval_hits : state.retrievalHits,
        webHits: typeof payload.web_hits === "number" ? payload.web_hits : state.webHits,
        error: payload.type === "run.failed" ? (payload.error ?? "DSH 请求失败") : state.error,
      };
      setLive(state);
    };
    const parseFrame = (frame: string) => {
      const lines = frame.split(/\r?\n/);
      const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (!data) return;
      try { applyEvent(JSON.parse(data) as GatewayEvent); } catch { applyEvent({ type: "run.failed", error: "DSH 返回了无法解析的事件" }); }
    };
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? "";
        frames.forEach(parseFrame);
      }
      buffer += decoder.decode();
      if (buffer.trim()) parseFrame(buffer);
    } finally { reader.releaseLock(); }
    state = { ...state, content: state.content || output };
    setLive(state);
    if (protocolError) throw new Error(protocolError);
    if (!terminalType) throw new Error("DSH 流式响应未正常结束");
    if (terminalType === "run.cancelled") throw new Error("DSH 会话已取消");
    if (state.error) throw new Error(state.error);
    return state;
  }

  async function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const text = query.trim();
    if ((!text && !images.length) || busy || !sessionId || !provider || !model) return;
    if (!effectiveSourceIds.length) { toast.error("请先使用 @ 选择至少一个知识库"); textareaRef.current?.focus(); return; }
    restoreComposerFocusRef.current = true;
    setBusy(true);
    setLive(EMPTY_LIVE);
    const pendingImages = images;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const uploaded = pendingImages.length ? await Promise.all(pendingImages.map((image) => api.uploadAttachment(image.file))) : [];
      const attachments = uploaded.map((item) => ({ id: item.id, name: item.name, media_type: item.media_type }));
      const userMessage = await api.dshCreateMessage(sessionId, { role: "user", content: text, attachments });
      if (effectiveSourceIds.some((sourceId) => !selected.includes(sourceId))) {
        setSelected(effectiveSourceIds);
        void api.dshPatchSession(sessionId, { source_ids: effectiveSourceIds });
      }
      setMessages((current) => [...current, userMessage]);
      setQuery("");
      setImages([]);
      pendingImages.forEach((image) => URL.revokeObjectURL(image.url));
      const result = await consumeStream(await api.dshStream({
        session_id: sessionId,
        query: text || "请分析附件图片。",
        source_ids: effectiveSourceIds,
        provider,
        model,
        ...(reasoning ? { reasoning_effort: reasoning } : {}),
        permission_preset: permission,
        mode,
        web_enabled: webEnabled,
        attachments,
      }, controller.signal));
      if (result.route.degraded) {
        const destination = result.route.mode === "agent-timeout-fallback" || result.route.mode === "agent-fallback" ? "受控 RAG" : "知识库回答";
        toast.warning(`已降级到${destination}`, { description: typeof result.route.degradation_reason === "string" ? result.route.degradation_reason : "上游服务未能完成原路线" });
      }
      const assistant = await api.dshCreateMessage(sessionId, {
        role: "assistant",
        content: result.content,
        retrieval_hits: result.retrievalHits,
        context: { ...result.context, citations: result.citations, route: result.route },
        event_summary: result.events.map((item) => ({
          type: item.type,
          query: item.query,
          round: item.round,
          retrieval_hits: item.retrieval_hits,
          web_hits: item.web_hits,
          coverage: item.coverage,
          source_count: item.source_count,
          strong_hits: item.strong_hits,
          max_rounds: item.max_rounds,
          rationale: item.rationale,
          error: item.error,
          reason: item.reason,
          route: item.route,
        })).slice(-100),
      });
      setMessages((current) => [...current, assistant]);
      setSessions((current) => current.map((item) => item.id === sessionId ? { ...item, title: item.title === "新 DSH 会话" ? sessionTitleFromQuery(text || "图片会话", selectedSourceRows) : item.title, updated_at: new Date().toISOString() } : item));
      window.dispatchEvent(new Event("sag:dsh-sessions-changed"));
      setLive(EMPTY_LIVE);
    } catch (error) {
      if (controller.signal.aborted) toast.message("已停止生成");
      else toast.error(error instanceof Error ? error.message : "DSH 会话请求失败");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
      if (restoreComposerFocusRef.current) {
        restoreComposerFocusRef.current = false;
        window.requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
      }
    }
  }

  async function archiveCurrent() {
    if (!sessionId || busy) return;
    try {
      const result = await api.dshArchiveSession(sessionId);
      toast.success("会话已归档", { description: `已保存 Markdown：${result.path}` });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "归档失败，原会话未改变");
      return;
    }
    const remaining = sessions.filter((item) => item.id !== sessionId);
    setSessions(remaining);
    window.dispatchEvent(new Event("sag:dsh-sessions-changed"));
    if (remaining[0]) await selectSession(remaining[0]);
    else await createSession();
  }

  async function saveAnswerToLibrary(message: DshMessageRecord) {
    if (!sessionId) return;
    try {
      const result = await api.dshSaveMessageToLibrary(sessionId, message.id);
      toast.success("回答已入库", { description: `已保存 Markdown：${result.path}` });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "回答入库失败");
    }
  }

  return (
    <TooltipProvider delayDuration={300}>
      <main className="flex h-full min-h-0 overflow-hidden bg-background text-foreground">
        {/* DSH 会话记录已迁移到全局侧边栏；保留这块代码，避免影响原有工作台逻辑。 */}
        <aside className="hidden w-64 shrink-0 flex-col border-r bg-muted/20" aria-hidden="true">
          <div className="flex h-14 items-center justify-between border-b px-3">
            <div className="flex min-w-0 items-center gap-2"><BrainCircuit className="size-5 shrink-0 text-primary" /><span className="truncate text-sm font-semibold">DSH 会话</span></div>
            <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" onClick={() => void createSession()} aria-label="新建 DSH 会话"><Plus /></Button></TooltipTrigger><TooltipContent>新建会话</TooltipContent></Tooltip>
          </div>
          <div className="flex-1 space-y-1 overflow-y-auto p-2">
            {sessions.map((session) => <button key={session.id} type="button" onClick={() => void selectSession(session)} className={cn("w-full rounded-md px-3 py-2 text-left transition-colors", session.id === sessionId ? "bg-background shadow-soft" : "hover:bg-muted")}>
              <div className="truncate text-sm font-medium">{titleForSession(session, sources)}</div>
              <div className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground"><span>{session.mode === "agent" ? "深度研究" : "RAG"}</span><span>·</span><span>{session.source_ids.length} 个知识库</span></div>
            </button>)}
          </div>
          <div className="border-t p-2 text-xs text-muted-foreground">SAG 检索 · DSH 编排</div>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
            <div className="min-w-0"><div className="truncate text-sm font-semibold">{currentSession ? titleForSession(currentSession, sources) : "加载会话"}</div><div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground"><span>{mode === "agent" ? "DSH 深度研究" : "DSH 受控问答"}</span>{webEnabled ? <><span>·</span><Globe2 className="size-3" /><span>联网</span></> : null}</div></div>
            <div className="flex items-center gap-1"><Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" onClick={() => void archiveCurrent()} disabled={busy} aria-label="归档会话"><Trash2 /></Button></TooltipTrigger><TooltipContent>归档会话</TooltipContent></Tooltip></div>
          </header>
          <div ref={transcriptRef} className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
            <div className="mx-auto flex max-w-3xl flex-col gap-6">
              <ConversationTranscript
                messages={displayTranscriptMessages}
                onCitationClick={openDshCitation}
                live={liveTranscript ? {
                  messageId: liveTranscript.id,
                  streaming: true,
                  steps: liveTranscript.steps,
                  collapsed: stepsCollapsed,
                  onToggle: () => setStepsCollapsed((value) => !value),
                } : undefined}
                assistantAvatar={<div className="mt-1 grid size-7 shrink-0 place-items-center rounded-md bg-primary text-primary-foreground"><Bot className="size-4" /></div>}
                renderUserAttachments={(message) => <UserAttachments attachments={message.attachments} />}
                onToolMatchClick={(match) => handleToolMatchClick(match)}
                renderAssistantFooter={({ message, previousUser }: ConversationMessageRenderContext<DshTranscriptMessage>) => (
                  <DshMessageActions
                    message={message}
                    citations={message.citations ?? []}
                    onSave={() => void saveAnswerToLibrary(message)}
                    onCitationClick={openDshCitation}
                    onRetry={previousUser?.content ? () => {
                      setQuery(previousUser.content);
                      textareaRef.current?.focus();
                    } : undefined}
                  />
                )}
                empty={!busy ? <div className="py-16 text-center"><div className="mx-auto grid size-12 place-items-center rounded-md bg-primary/10 text-primary"><Sparkles className="size-6" /></div><h1 className="mt-4 text-xl font-semibold">DSH 知识会话</h1><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">用 <strong className="font-medium text-foreground">@</strong> 添加一个或多个 SAG 知识库，用 <strong className="font-medium text-foreground">/</strong> 切换研究方式；DSH 会在受控范围内组织检索与答案。</p></div> : null}
              />
            </div>
          </div>
          <form onSubmit={(event) => void submit(event)} className="shrink-0 border-t bg-background p-3 sm:p-4">
            <div className="relative mx-auto max-w-3xl rounded-md border bg-muted/20 p-2 shadow-soft focus-within:ring-2 focus-within:ring-primary/30">
              {selectedSourceRows.length ? <div className="flex flex-wrap gap-1.5 px-1 pb-2">{selectedSourceRows.map((source) => <button key={source.id} type="button" onClick={() => toggleSource(source)} className="inline-flex h-6 items-center gap-1 rounded-sm border bg-background px-2 text-xs hover:bg-muted"><AtSign className="size-3 text-primary" />{source.name}<X className="size-3" /></button>)}</div> : null}
              {images.length ? <div className="flex flex-wrap gap-2 px-1 pb-2">{images.map((image) => <div key={image.url} className="relative"><img src={image.url} alt={image.file.name} className="size-16 rounded-md border object-cover" /><button type="button" onClick={() => removeImage(image.url)} className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-foreground text-background"><X className="size-3" /></button></div>)}</div> : null}
              {mentionOpen && mentionCandidates.length ? <div className="absolute bottom-full left-0 z-20 mb-2 w-[min(28rem,calc(100vw-2rem))] overflow-hidden rounded-md border bg-popover p-1 shadow-lift">{mentionCandidates.map((source) => <button key={source.id} type="button" onClick={() => chooseMention(source)} className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left hover:bg-muted"><Database className="size-4 text-primary" /><span className="min-w-0 flex-1 truncate text-sm">{source.name}</span><span className="text-xs text-muted-foreground">{source.document_count} 文档</span></button>)}</div> : null}
              {commandOpen && commandCandidates.length ? <div className="absolute bottom-full left-0 z-20 mb-2 w-[min(30rem,calc(100vw-2rem))] overflow-hidden rounded-md border bg-popover p-1 shadow-lift">{commandCandidates.map((item) => <button key={item.command} type="button" onClick={() => chooseCommand(item.action)} className="flex w-full items-center gap-3 rounded-sm px-3 py-2 text-left hover:bg-muted"><Command className="size-4 text-primary" /><span><span className="block text-sm font-medium">{item.command} <span className="font-normal text-muted-foreground">{item.title}</span></span><span className="block text-xs text-muted-foreground">{item.hint}</span></span></button>)}</div> : null}
              <textarea ref={textareaRef} value={query} onChange={(event) => { const value = event.target.value; setQuery(value); setMentionOpen(/(?:^|\s)@[^\s@]*$/.test(value)); setCommandOpen(value.trim().startsWith("/")); }} onCompositionStart={startComposition} onCompositionEnd={endComposition} onPaste={(event) => { const files = Array.from(event.clipboardData.files); if (files.length) { event.preventDefault(); addImages(files); } }} onKeyDown={(event) => { if (isComposingKeyEvent(event)) return; if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } if (event.key === "Escape") { setMentionOpen(false); setCommandOpen(false); } }} placeholder="输入问题，@ 添加知识库，/ 切换模式" className="min-h-16 max-h-36 w-full resize-y bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground" />
              <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
                <div className="flex min-w-0 flex-wrap items-center gap-1">
                  <Tooltip><TooltipTrigger asChild><label className="inline-flex size-8 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"><ImagePlus className="size-4" /><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple className="hidden" onChange={(event) => { addImages(event.target.files); event.currentTarget.value = ""; }} /></label></TooltipTrigger><TooltipContent>添加图片</TooltipContent></Tooltip>
                  <button type="button" onClick={() => setWebEnabled((value) => { const next = !value; applySessionConfig({ web_enabled: next }); return next; })} className={cn("inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs", webEnabled ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}><Globe2 className="size-3.5" />联网</button>
                  <button type="button" onClick={() => { const next = mode === "controlled" ? "agent" : "controlled"; setMode(next); applySessionConfig({ mode: next }); }} className={cn("inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs", mode === "agent" ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}><WandSparkles className="size-3.5" />{mode === "agent" ? "深度研究" : "RAG"}</button>
                  <select value={provider} onChange={(event) => { const next = event.target.value; const first = capabilities?.groups.find((group) => group.id === next)?.models[0]; setProvider(next); setModel(first?.id ?? ""); applySessionConfig({ provider: next, model: first?.id ?? "" }); }} className="h-8 max-w-28 rounded-md border bg-background px-2 text-xs" aria-label="DSH 模型服务商">
                    {capabilities?.groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                  </select>
                  <select value={model} onChange={(event) => { setModel(event.target.value); applySessionConfig({ model: event.target.value }); }} className="h-8 max-w-36 rounded-md border bg-background px-2 text-xs" aria-label="DSH 模型">
                    {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                  {currentModel?.reasoning_efforts?.length ? <select value={reasoning} onChange={(event) => setReasoning(event.target.value)} className="h-8 max-w-24 rounded-md border bg-background px-2 text-xs" aria-label="思考强度"><option value="">默认思考</option>{currentModel.reasoning_efforts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select> : null}
                  <select value={permission} onChange={(event) => { setPermission(event.target.value); applySessionConfig({ permission_preset: event.target.value }); }} className="h-8 max-w-32 rounded-md border bg-background px-2 text-xs" aria-label="DSH 权限预设">
                    {capabilities?.permission_presets.map((item) => <option key={item} value={item}>{item}</option>)}
                  </select>
                </div>
                {busy ? <Button type="button" variant="outline" size="icon" onClick={() => abortRef.current?.abort()} aria-label="停止生成"><Square /></Button> : <Button type="submit" size="icon" disabled={(!query.trim() && !images.length) || !sessionId || !provider || !model} aria-label="发送"><Send /></Button>}
              </div>
            </div>
          </form>
          <div className="shrink-0 border-t bg-muted/20 px-4 py-2 text-xs text-muted-foreground sm:px-8">
            <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-1">
              <span>上下文 {contextTokens ? `${Math.round(contextTokens).toLocaleString()} / ${contextWindow.toLocaleString()}` : "等待注入"}</span>
              <Progress value={contextPercent} className="h-1 w-20" />
              <span>命中分块 {live.retrievalHits || lastAssistant?.retrieval_hits || 0}</span>
              <span>流转事件 {live.events.length || lastAssistant?.event_summary.length || 0}</span>
              <span>缓存命中率 {typeof runRoute.cache_hit_rate === "number" ? `${Math.round(runRoute.cache_hit_rate * 100)}%` : "--"}</span>
              <span>Token {readNumber(runContext)?.toLocaleString() ?? "--"}</span>
              {typeof runRoute.elapsed_ms === "number" ? <span>耗时 {(runRoute.elapsed_ms / 1000).toFixed(1)}s</span> : null}
            </div>
          </div>
        </section>

        {/* 右侧设置代码已隐藏；模型、权限、统计都已移入对话工作区。 */}
      </main>
    </TooltipProvider>
  );
}
