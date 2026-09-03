export type WorkspaceSection = "search" | "answer" | "knowledge" | "projects" | "dsh";

export interface WorkspaceSectionDefinition {
  id: WorkspaceSection;
  href: string;
  shortcut?: string;
}

/**
 * 工作台能力的单一入口配置。normal 与 mini 只改变呈现方式，不再各自维护菜单。
 */
export const WORKSPACE_SECTIONS: readonly WorkspaceSectionDefinition[] = [
  { id: "search", href: "/search", shortcut: "⌘K" },
  { id: "answer", href: "/chat", shortcut: "⌘J" },
  { id: "knowledge", href: "/knowledge" },
  // 暂停展示“工作台”入口；保留 /projects 页面与项目管理功能，恢复时取消下一行注释即可。
  // { id: "projects", href: "/projects" },
  // DSH 会话入口与原生问答一致：从工作台菜单进入时总是创建新会话。
  { id: "dsh", href: "/dsh?new=1" },
];

export function isWorkspaceSection(value: unknown): value is WorkspaceSection {
  return value === "search" || value === "answer" || value === "knowledge" || value === "projects" || value === "dsh";
}

export function workspaceSectionFromPathname(pathname: string): WorkspaceSection | null {
  if (pathname === "/search" || pathname.startsWith("/search/")) return "search";
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return "answer";
  if (pathname === "/knowledge" || pathname.startsWith("/knowledge/")) return "knowledge";
  if (pathname === "/projects" || pathname.startsWith("/projects/")) return "projects";
  if (pathname === "/dsh" || pathname.startsWith("/dsh/")) return "dsh";
  return null;
}

export function workspaceSectionDefinition(section: WorkspaceSection) {
  return WORKSPACE_SECTIONS.find((item) => item.id === section)!;
}
