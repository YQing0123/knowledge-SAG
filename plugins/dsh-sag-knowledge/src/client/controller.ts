/**
 * SAG 面板控制器（客户端外部 store，任务板同款模式）。
 *
 * state 形状：
 * - open     ：shell.overlay 面板是否打开（侧边栏按钮切换）
 * - mainTab  ：面板首页顶部标签（'search' 知识库搜索 | 'manage' 知识库管理）
 * - settingsTab：设置分区内标签（knowledge/model/integrations/graph/diagnostics）
 *
 * 用 useSyncExternalStore 订阅（任务板 Nav/Page 同一套 API）。
 */
export type SagMainTab = 'search' | 'manage' | 'agent'
export type SagSettingsTab = 'knowledge' | 'model' | 'integrations' | 'graph' | 'diagnostics'

export interface SagPanelState {
  open: boolean
  mainTab: SagMainTab
  agentSourceIds: readonly string[]
  settingsTab: SagSettingsTab
}

const initialState: SagPanelState = { open: false, mainTab: 'search', agentSourceIds: [], settingsTab: 'knowledge' }

export class SagPanelController {
  private state: SagPanelState = initialState
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getSnapshot = (): SagPanelState => this.state

  private emit(): void {
    for (const listener of this.listeners) listener()
  }

  open(): void {
    if (this.state.open) return
    this.state = { ...this.state, open: true }
    this.emit()
  }

  close(): void {
    if (!this.state.open) return
    this.state = { ...this.state, open: false }
    this.emit()
  }

  toggle(): void {
    this.state = { ...this.state, open: !this.state.open }
    this.emit()
  }

  setMainTab(tab: SagMainTab): void {
    if (this.state.mainTab === tab) return
    this.state = { ...this.state, mainTab: tab }
    this.emit()
  }

  addAgentSourceId(sourceId: string): void {
    if (this.state.agentSourceIds.includes(sourceId)) return
    this.state = { ...this.state, agentSourceIds: [...this.state.agentSourceIds, sourceId] }
    this.emit()
  }

  setSettingsTab(tab: SagSettingsTab): void {
    if (this.state.settingsTab === tab) return
    this.state = { ...this.state, settingsTab: tab }
    this.emit()
  }
}

/** 单一实例（client bundle 内模块级单例，供 Nav / Page / SettingsSection 共享）。 */
export const sagPanel = new SagPanelController()

/** client 插件上下文（含 slots）。 */
export interface SagClientContext {
  slots: {
    inject(name: string, factory: () => unknown): unknown
    register(options: Record<string, unknown>, component: unknown): () => void
  }
}