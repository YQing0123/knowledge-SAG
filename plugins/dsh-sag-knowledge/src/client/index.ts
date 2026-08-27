/**
 * @dsh-external/dsh-sag-knowledge — client 半（浏览器）。
 *
 * 服务注入：
 *  - slots            : UI 槽位（sidebar.footer.action / shell.overlay / settings.section）
 *  - inputTriggers    : 输入触发（@）候选管线，注册 SAG 知识库 @ 候选
 *
 * 注册项：
 *  - sidebar.footer.action : SAG 导航按钮（order:-10，与任务板上下排）
 *  - shell.overlay         : 首页面板（顶部 tabs 知识库搜索/知识库管理 + embed iframe）
 *  - settings.section      : 设置页「SAG 知识库」分区（直接 iframe SAG 设置页）
 *  - inputTriggers @ 源    : 输入 `@` 弹出 SAG 知识库候选（@ 选库）
 *
 * 构建：tsdown → lib/client.js，react 外置，由 window.__ModuleLoader__.load 注册。
 */
import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import { registerSagSourceMention } from './SagMentions.ts'
import { sagPanel } from './controller.ts'
import { SagNav, SagPage, SagSettingsSection } from './SagPanel.tsx'

export const inject = ['slots', 'inputTriggers', 'sessions']

export function apply(ctx: ClientContext): void {
  // 输入触发：@ 弹出 SAG 知识库候选（真实候选列表，非提示词约定）
  registerSagSourceMention(ctx)

  // Match taskboard behavior: selecting another DSH session returns to that
  // conversation immediately instead of leaving the overlay over it.
  const sessions = ctx.get('sessions') as { list: { getSnapshot(): { current?: string }; subscribe(listener: () => void): () => void } }
  let previousSession = sessions.list.getSnapshot().current
  ctx.effect(() => sessions.list.subscribe(() => {
    const nextSession = sessions.list.getSnapshot().current
    if (nextSession !== previousSession) {
      previousSession = nextSession
      if (sagPanel.getSnapshot().open) sagPanel.close()
    }
  }), 'sag-knowledge: close overlay on DSH session navigation')

  // 侧边栏底部动作：SAG 导航按钮（order:-10 置于任务板之上，左下角，上下排）
  ctx.slots.inject('sidebar.footer.action', () =>
    ctx.slots.register({ name: 'sidebar.footer.action', id: 'sag-knowledge.nav', order: -10, label: () => 'SAG 知识库' }, SagNav),
  )

  // 首页面板：顶部 tabs（知识库搜索 | 知识库管理），主体 embed iframe
  ctx.slots.inject('shell.overlay', () =>
    ctx.slots.register({ name: 'shell.overlay', id: 'sag-knowledge.page', order: -10, label: () => 'SAG 知识库' }, SagPage),
  )

  // 设置分区：直接 iframe SAG 设置页（embed 模式），SAG 内部自带设置 tab
  ctx.slots.inject('settings.section', () =>
    ctx.slots.register({ name: 'settings.section', id: 'sag-knowledge', order: 40, label: () => 'SAG 知识库' }, SagSettingsSection),
  )
}

export default { inject, apply }