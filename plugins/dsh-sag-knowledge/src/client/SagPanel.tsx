/**
 * @dsh-external/dsh-sag-knowledge — client 组件（任务板同款形态）。
 *
 * 三个注册点：
 *  - SagNav              → sidebar.footer.action（侧边栏底部动作，order:-10 与任务板上下排）
 *  - SagPage             → shell.overlay（任务板式中栏嵌入：左侧 DSH 工作区保留，
 *                          页面只占中栏；顶部 tabs [知识库搜索|知识库管理] + iframe）
 *  - SagSettingsSection  → settings.section（设置分区：直接 iframe SAG 设置页，
 *                          SAG 内部自带 8 个 tab，本插件不再包外层 tab）
 *
 * 「embed 模式」：SAG Web 支持 ?embed=1（apps/web/app/(app)/layout.tsx → AppShell）——
 * 隐藏 SAG 左侧主菜单（AppSidebar）与顶部行（SiteHeader：侧边栏收起/面包屑/探索），
 * 只保留内容区；iframe 全宽显示，SAG 内部次级侧边栏（筛选/知识库树）可自适应展开收缩，
 * 收起图标随主菜单一起隐藏，无需 CSS 负偏移裁剪。
 *
 * 主机名约束：iframe 基址由 window.location.hostname 推导（http://<同主机名>:3000），
 * 保证与 DSH GUI 同站 → SAG SameSite=Lax cookie 正常下发，登录态保持。
 */
import { useEffect, useRef, useState } from 'react'
import { useSyncExternalStore } from 'react'
import { sagPanel, type SagMainTab } from './controller.ts'

/** SAG Web 端口（compose 默认）。 */
const SAG_WEB_PORT = 3000

/** 从 DSH 当前页面主机名推导 SAG Web 基址（同站关键）。 */
function sagWebBase(): string {
  return `http://${window.location.hostname}:${SAG_WEB_PORT}`
}

/** embed 标记：SAG 隐藏主菜单与顶部行。 */
function embedQuery(path: string): string {
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}embed=1`
}

/** ── embed iframe：完整宽显示 SAG 内容区 ── */
function SagIframe({ path }: { path: string }) {
  const src = `${sagWebBase()}${embedQuery(path)}`
  return (
    <iframe
      title="SAG"
      src={src}
      style={{
        width: '100%',
        height: '100%',
        border: 0,
        display: 'block',
        background: 'var(--dsw-alias-bg-page, #fff)',
      }}
    />
  )
}

/**
 * 任务板同款几何：读取 DSH 外壳 grid 列宽，页面只占中栏。
 * 复刻 @shengsheng/dsh-taskboard 的 useFrameInsets——Page 根 → overlay 容器 → frame。
 */
function useFrameInsets(ref: React.RefObject<HTMLElement | null>, active: boolean) {
  const [insets, setInsets] = useState({ left: 0, right: 0 })
  useEffect(() => {
    if (!active || ref.current === null) return
    const frame = ref.current.parentElement?.parentElement?.parentElement
    if (frame === null || frame === undefined) return
    const measure = () => {
      const tracks = getComputedStyle(frame).gridTemplateColumns.split(' ')
      const left = Number.parseFloat(tracks[0] ?? '0')
      const right = Number.parseFloat(tracks[tracks.length - 1] ?? '0')
      setInsets({
        left: Number.isFinite(left) ? left : 0,
        right: Number.isFinite(right) ? right : 0,
      })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(frame)
    return () => {
      observer.disconnect()
    }
  }, [active, ref])
  return insets
}

/** ── 侧边栏底部按钮（与任务板上下排，order:-10）── */
export function SagNav({ wide }: { wide: boolean }) {
  const state = useSyncExternalStore(sagPanel.subscribe, sagPanel.getSnapshot, sagPanel.getSnapshot)
  const buttonRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const onTaskboardClick = (event: Event) => {
      const target = event.target
      if (target instanceof Element && target.closest('.dsh-taskboard-nav')) sagPanel.close()
    }
    document.addEventListener('click', onTaskboardClick, true)
    return () => document.removeEventListener('click', onTaskboardClick, true)
  }, [])

  useEffect(() => {
    const button = buttonRef.current
    if (!button) return

    let owner: HTMLElement | null = null
    let node: HTMLElement | null = button.parentElement
    while (node) {
      if (getComputedStyle(node).display === 'flex') {
        owner = node
        break
      }
      node = node.parentElement
    }
    if (!owner) return

    const prior = [owner.style.flexDirection, owner.style.alignItems, owner.style.flexWrap]
    const arrange = () => {
      owner!.style.flexDirection = 'column'
      owner!.style.alignItems = 'stretch'
      owner!.style.flexWrap = 'nowrap'
    }
    arrange()
    const observer = new MutationObserver(arrange)
    observer.observe(owner, { childList: true })
    return () => {
      observer.disconnect()
      owner!.style.flexDirection = prior[0]
      owner!.style.alignItems = prior[1]
      owner!.style.flexWrap = prior[2]
    }
  }, [])
  return (
    <>
      <style>{`
        /* 侧边栏底部：footer.action 容器是 CSS Modules（哈希类名），不能按 .footerActions 覆盖。
           用 :has() 命中包含 SAG 按钮的父容器（含 DSH 内置的任务板按钮），强制纵向排列；
           两个按钮 flex:0 0 auto + max-width:100% 保证各占一行、互不挤压。
           该规则只影响同时包含 .dsh-sag-nav 的父容器，不会波及其它区块。 */
        div:has(> .dsh-sag-nav):has(> .dsh-taskboard-nav){flex-direction:column!important;align-items:stretch!important}
        div:has(> .dsh-sag-nav){flex-direction:column!important;align-items:stretch!important}
        .dsh-sag-nav,.dsh-taskboard-nav{flex:0 0 auto;max-width:100%}
        .dsh-sag-nav{-webkit-appearance:none;appearance:none;flex:none;display:flex;align-items:center;gap:8px;
          width:calc(100% + 8px);height:34px;margin:4px -4px;padding:6px 2px 6px 10px;box-sizing:border-box;
          border:none;border-radius:12px;background:transparent;box-shadow:none;
          color:var(--dsw-alias-label-primary,#0f1115);cursor:pointer;font:inherit;font-size:14px;line-height:22px;overflow:hidden}
        .dsh-sag-nav:hover,.dsh-sag-nav[data-active=true]{background:var(--dsw-alias-interactive-bg-hover,rgba(38,49,72,.06))}
        .dsh-sag-nav-rail{width:36px;height:36px;margin:8px 0 10px;justify-content:center;gap:0;padding:0;border-radius:50%}
        .dsh-sag-nav-label{overflow:hidden;white-space:nowrap}
      `}</style>
      <button
        ref={buttonRef}
        type="button"
        data-active={state.open}
        className={wide ? 'dsh-sag-nav' : 'dsh-sag-nav dsh-sag-nav-rail'}
        aria-label="SAG 知识库"
        aria-pressed={state.open}
        onClick={() => {
           const taskboard = document.querySelector<HTMLButtonElement>('.dsh-taskboard-nav[aria-pressed="true"]')
           if (taskboard) taskboard.click()
           sagPanel.toggle()
         }}
      >
        <svg width={wide ? 16 : 18} height={wide ? 16 : 18} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
          <path d="M2.5 2C2.5 1.45 2.95 1 3.5 1h9c.55 0 1 .45 1 1v12c0 .55-.45 1-1 1h-9c-.55 0-1-.45-1-1V2Zm2 0v1h7V2h-7Zm0 3v7h7V5h-7Zm1 1.5h5v1h-5v-1Zm0 2.5h5v1h-5v-1Z" />
        </svg>
        {wide && <span className="dsh-sag-nav-label">SAG 知识库</span>}
      </button>
    </>
  )
}

/** 顶部标签页（搜索/管理）。 */
const MAIN_TABS: Array<{ key: SagMainTab; label: string }> = [
  { key: 'search', label: '知识库搜索' },
  { key: 'manage', label: '知识库管理' },
  { key: 'agent', label: '原生 Agent 会话' },
]

/** ── 任务板式中栏面板：顶部 tabs + embed iframe，左侧 DSH 工作区保留 ── */
export function SagPage() {
  const state = useSyncExternalStore(sagPanel.subscribe, sagPanel.getSnapshot, sagPanel.getSnapshot)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const insets = useFrameInsets(rootRef, state.open)
  if (!state.open) return null

  const agentQuery = state.agentSourceIds.length ? `?source_ids=${encodeURIComponent(state.agentSourceIds.join(','))}` : ''
  const path = state.mainTab === 'manage' ? '/knowledge' : state.mainTab === 'agent' ? `/chat${agentQuery}${agentQuery ? '&' : '?'}embed_sidebar=1` : '/search'
  return (
    <div
      ref={rootRef}
      className="dsh-sag-page"
      style={{ left: insets.left, right: insets.right }}
      role="dialog"
      aria-modal="true"
      aria-label="SAG 知识库"
    >
      <style>{`
        .dsh-sag-page{position:absolute;top:0;bottom:0;z-index:1;display:flex;flex-direction:column;
          overflow:hidden;background:var(--dsw-alias-bg-page,#fff);
          --dsh-scrollbar-thumb:var(--dsw-alias-scrollbar-bg-l2,#d4d4d4)}
        .dsh-sag-page-header{flex:none;display:flex;align-items:center;gap:8px;height:48px;padding:0 12px;
          border-bottom:1px solid var(--dsw-alias-border-l1,rgba(0,0,0,.08))}
        .dsh-sag-page-title{font-size:14px;font-weight:600;margin-right:8px;
          color:var(--dsw-alias-label-primary,#1f2329)}
        .dsh-sag-page-tabs{display:flex;gap:4px;flex:1}
        .dsh-sag-page-tabs button{height:32px;padding:0 14px;border:0;border-radius:10px;
          background:transparent;font:inherit;font-size:13px;cursor:pointer;
          color:var(--dsw-alias-label-secondary,#61666b)}
        .dsh-sag-page-tabs button[aria-current=page]{background:var(--dsw-specific-sidebar-nav-item-active,#ebeef2);
          color:var(--dsw-alias-label-primary,#1f2329)}
        .dsh-sag-page-close{height:32px;padding:0 10px;border:0;border-radius:10px;cursor:pointer;
          background:transparent;color:var(--dsw-alias-label-secondary,#61666b);
          font:inherit;font-size:13px}
        .dsh-sag-page-close:hover{background:var(--dsw-specific-sidebar-nav-item-hover,#f1f3f5)}
      `}</style>
      <header className="dsh-sag-page-header">
        <span className="dsh-sag-page-title">SAG 知识库</span>
        <nav className="dsh-sag-page-tabs" aria-label="SAG 知识库">
          {MAIN_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              aria-current={state.mainTab === tab.key ? 'page' : undefined}
              onClick={() => { sagPanel.setMainTab(tab.key) }}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <button type="button" className="dsh-sag-page-close" onClick={() => { sagPanel.close() }}>
          关闭
        </button>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        <SagIframe path={path} />
      </div>
    </div>
  )
}

/** ── 设置页分区：直接 iframe SAG 设置页（内部自带 8 tab，无外层包装）── */
export function SagSettingsSection(_props: { close?(): void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <style>{`
        .dsh-sag-settings{flex:1;min-height:0}
      `}</style>
      <div className="dsh-sag-settings">
        <SagIframe path="/settings" />
      </div>
    </div>
  )
}

export default { SagNav, SagPage, SagSettingsSection }