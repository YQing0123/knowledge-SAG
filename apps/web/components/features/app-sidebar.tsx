"use client";

import * as React from "react";
import Image from "next/image";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import {
  Archive,
  BrainCircuit,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  LogOut,
  MessageSquarePlus,
  Settings,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { api, ApiError, type DshSessionRecord } from "@/lib/api";
import { PRODUCT_NAME } from "@/lib/branding";
import { relativeTime } from "@/lib/format";
import {
  WORKSPACE_SECTIONS,
  workspaceSectionFromPathname,
} from "@/lib/workspace";
import { useApp } from "@/components/features/app-shell";
import { useConversationIndex } from "@/components/features/chat/conversation-provider";
import { WorkspaceSectionIcon } from "@/components/features/workspace-section-icon";
import { DesktopUpdateIndicator } from "@/components/features/desktop-update-indicator";
import { AppVersionBadge } from "@/components/features/app-version-badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

function Brand() {
  const t = useTranslations("AppSidebar");
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" className="h-14 gap-2.5" tooltip={PRODUCT_NAME} asChild>
          <Link href="/chat">
            <Image
              src="/sag-icon.png"
              alt=""
              width={32}
              height={32}
              priority
              aria-hidden="true"
              className="size-8 shrink-0 rounded-[9px] shadow-sm ring-1 ring-black/10 dark:ring-white/10"
            />
            <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-base font-semibold">{PRODUCT_NAME}</span>
                <AppVersionBadge />
              </span>
              <span className="truncate text-xs text-muted-foreground">{t("brandTagline")}</span>
            </div>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

function NavUser() {
  const t = useTranslations("AppSidebar");
  const { user, logout } = useApp();
  const initial = (user?.name || user?.email || "?").slice(0, 1).toUpperCase();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <div className="flex aspect-square size-8 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                {initial}
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{user?.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {user?.email || t("localIdentity")}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto size-4 text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-56"
            side="top"
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col">
                <span className="truncate text-sm font-medium">{user?.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {user?.email || t("localIdentity")}
                </span>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <Settings className="size-4" />
                {t("identitySettings")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={logout} className="text-destructive focus:text-destructive">
              <LogOut className="size-4" />
              {t("signOut")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

export function AppSidebar({ contained = false }: { contained?: boolean }) {
  const t = useTranslations("AppSidebar");
  const nav = useTranslations("Navigation");
  const locale = useLocale();
  const routePath = usePathname();
  const router = useRouter();
  const {
    agent,
    threads,
    hasMoreThreads,
    threadsExpanded,
    loadingMoreThreads,
    refreshThreads,
    loadMoreThreads,
    collapseThreads,
    capabilities,
    timezone,
  } = useApp();
  const conversationIndex = useConversationIndex();
  const runningThreads = React.useMemo(
    () =>
      new Set(
        conversationIndex.sessions.flatMap((session) =>
          session.running && session.threadId ? [session.threadId] : [],
        ),
      ),
    [conversationIndex.sessions],
  );

  // replaceState（新会话接管 URL 不打断流式）不会触发 usePathname —— 监听自定义事件补齐
  const [pathname, setPathname] = React.useState(routePath);
  React.useEffect(() => setPathname(routePath), [routePath]);
  React.useEffect(() => {
    const sync = () => setPathname(window.location.pathname);
    window.addEventListener("sag:pathchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("sag:pathchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);

  const activeThreadId = pathname.startsWith("/chat/") ? pathname.split("/")[2] : null;
  const activeDshSessionId = pathname.startsWith("/dsh/") ? pathname.split("/")[2] : null;
  const [nativeOpen, setNativeOpen] = React.useState(true);
  const [dshOpen, setDshOpen] = React.useState(true);
  const [dshSessions, setDshSessions] = React.useState<DshSessionRecord[]>([]);
  const [dshExpanded, setDshExpanded] = React.useState(false);

  const loadDshSessions = React.useCallback(async () => {
    const rows = await api.dshListSessions();
    setDshSessions(rows.filter((session) => !session.archived));
  }, []);

  React.useEffect(() => {
    if (!capabilities?.dsh_enabled) {
      setDshSessions([]);
      return;
    }
    void loadDshSessions().catch(() => undefined);
    const refresh = () => void loadDshSessions().catch(() => undefined);
    window.addEventListener("sag:dsh-sessions-changed", refresh);
    window.addEventListener("sag:dsh-integration-changed", refresh);
    return () => {
      window.removeEventListener("sag:dsh-sessions-changed", refresh);
      window.removeEventListener("sag:dsh-integration-changed", refresh);
    };
  }, [capabilities?.dsh_enabled, loadDshSessions]);

  async function archiveThread(tid: string) {
    if (!agent) return;
    try {
      const exported = await api.exportThreadMarkdown(agent.id, tid);
      await api.updateThread(agent.id, tid, { archived: true });
      await refreshThreads();
      if (activeThreadId === tid) router.push("/chat");
      toast.success(t("archived"), { description: `已保存 Markdown：${exported.path}` });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("archiveFailed"));
    }
  }

  async function deleteThread(tid: string) {
    if (!agent) return;
    try {
      await api.deleteThread(agent.id, tid);
      await refreshThreads();
      if (activeThreadId === tid) router.push("/chat");
      toast.success(t("threadDeleted"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("deleteFailed"));
    }
  }

  async function revealMoreThreads() {
    try {
      await loadMoreThreads();
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("loadMoreFailed"));
    }
  }

  async function archiveDshSession(session: DshSessionRecord) {
    try {
      const result = await api.dshArchiveSession(session.id);
      setDshSessions((current) => current.filter((item) => item.id !== session.id));
      if (activeDshSessionId === session.id) router.push("/dsh?new=1");
      window.dispatchEvent(new Event("sag:dsh-sessions-changed"));
      toast.success("会话已归档", { description: `已保存 Markdown：${result.path}` });
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "会话归档失败，未改变原会话");
    }
  }

  async function deleteDshSession(session: DshSessionRecord) {
    try {
      await api.dshDeleteSession(session.id);
      setDshSessions((current) => current.filter((item) => item.id !== session.id));
      if (activeDshSessionId === session.id) router.push("/dsh?new=1");
      window.dispatchEvent(new Event("sag:dsh-sessions-changed"));
      toast.success("DSH 会话已删除");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "DSH 会话删除失败");
    }
  }

  const activeSection = workspaceSectionFromPathname(pathname);

  return (
    <Sidebar collapsible="icon" className={contained ? "absolute" : undefined}>
      <SidebarHeader>
        <Brand />
      </SidebarHeader>
      <SidebarContent className="overflow-hidden">
        <SidebarGroup className="shrink-0">
          <SidebarMenu>
            {WORKSPACE_SECTIONS.filter((item) => item.id !== "dsh" || capabilities?.dsh_enabled).map((item) => {
              return (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton
                    asChild
                    isActive={activeSection === item.id}
                    tooltip={nav(item.id)}
                  >
                    <Link href={item.href}>
                      <WorkspaceSectionIcon section={item.id} />
                      <span>{nav(item.id)}</span>
                      {item.shortcut && (
                        <kbd className="ml-auto hidden rounded border border-sidebar-border px-1 py-0.5 text-[10px] font-medium leading-none text-muted-foreground opacity-0 transition-opacity group-hover/menu-item:opacity-100 group-data-[collapsible=icon]:hidden sm:inline-flex">
                          {item.shortcut}
                        </kbd>
                      )}
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </SidebarGroup>

        <SidebarGroup
          className={`min-h-0 overflow-hidden group-data-[collapsible=icon]:hidden ${
            capabilities?.dsh_enabled ? "" : "flex-1"
          }`}
        >
          <SidebarGroupLabel className="flex items-center pr-1">
            <button type="button" onClick={() => setNativeOpen((value) => !value)} className="flex min-w-0 flex-1 items-center gap-1 text-left" aria-expanded={nativeOpen}>
              <span className="truncate">原生问答</span>
              <ChevronDown className={nativeOpen ? "size-3.5" : "size-3.5 -rotate-90"} />
            </button>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => {
                    window.dispatchEvent(new Event("sag:new-chat"));
                    router.push("/chat");
                  }}
                  aria-label={t("newChat")}
                  className="grid size-6 place-items-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                >
                  <MessageSquarePlus className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right">{t("newChat")}</TooltipContent>
            </Tooltip>
          </SidebarGroupLabel>
          {nativeOpen ? <SidebarMenu
            className={`min-h-0 overflow-y-auto pr-1 ${
              capabilities?.dsh_enabled ? "max-h-[35vh]" : "flex-1"
            }`}
          >
            {threads.length === 0 && (
              <p className="px-2 py-1.5 text-xs text-muted-foreground">
                {t("emptyThreads")}
              </p>
            )}
            {threads.map((thread) => {
              const isLive = runningThreads.has(thread.id);
              return (
                <SidebarMenuItem key={thread.id}>
                  <SidebarMenuButton asChild isActive={thread.id === activeThreadId}>
                    <Link href={`/chat/${thread.id}`} aria-label={thread.title}>
                      <span className="min-w-0 flex-1 truncate">{thread.title}</span>
                      {isLive ? (
                        <Spinner
                          className="size-3 shrink-0 text-muted-foreground"
                          aria-label={t("generating")}
                        />
                      ) : (
                        <span className="ml-2 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground transition-opacity group-focus-within/menu-item:opacity-0 group-hover/menu-item:opacity-0">
                          {relativeTime(thread.updated_at, timezone, locale)}
                        </span>
                      )}
                    </Link>
                  </SidebarMenuButton>
                  {!isLive && (
                    <>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              void deleteThread(thread.id);
                            }}
                            aria-label={t("deleteThread")}
                            className="absolute right-8 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded-md text-muted-foreground opacity-0 outline-none transition-[opacity,color,background-color] hover:bg-sidebar-accent hover:text-destructive focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring group-hover/menu-item:opacity-100"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">{t("deleteThread")}</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              void archiveThread(thread.id);
                            }}
                            aria-label={t("archiveThread")}
                            className="absolute right-2 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded-md text-muted-foreground opacity-0 outline-none transition-[opacity,color,background-color] hover:bg-sidebar-accent hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring group-hover/menu-item:opacity-100"
                          >
                            <Archive className="size-3.5" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">{t("archive")}</TooltipContent>
                      </Tooltip>
                    </>
                  )}
                </SidebarMenuItem>
              );
            })}
            {hasMoreThreads ? (
              <SidebarMenuItem className="pt-1">
                <div className="flex items-center gap-1">
                  <SidebarMenuButton
                    type="button"
                    size="sm"
                    disabled={loadingMoreThreads}
                    aria-label={t("expandMore")}
                    onClick={() => void revealMoreThreads()}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {loadingMoreThreads ? t("loading") : t("more")}
                    </span>
                    {loadingMoreThreads ? <Spinner className="size-3.5" /> : <ChevronDown />}
                  </SidebarMenuButton>
                  {threadsExpanded && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label={t("collapse")}
                          onClick={collapseThreads}
                          className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                        >
                          <ChevronUp className="size-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="right">{t("collapse")}</TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </SidebarMenuItem>
            ) : threadsExpanded ? (
              <SidebarMenuItem className="pt-1">
                <SidebarMenuButton
                  type="button"
                  size="sm"
                  onClick={collapseThreads}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <span className="min-w-0 flex-1 truncate">{t("collapse")}</span>
                  <ChevronUp />
                </SidebarMenuButton>
              </SidebarMenuItem>
            ) : null}
          </SidebarMenu> : null}
        </SidebarGroup>

        {capabilities?.dsh_enabled ? <SidebarGroup className="min-h-0 flex-1 overflow-hidden group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel className="flex items-center pr-1">
            <button type="button" onClick={() => setDshOpen((value) => !value)} className="flex min-w-0 flex-1 items-center gap-1 text-left" aria-expanded={dshOpen}>
              <span className="truncate">DSH会话</span>
              <ChevronDown className={dshOpen ? "size-3.5" : "size-3.5 -rotate-90"} />
            </button>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => router.push("/dsh?new=1")}
                  aria-label="新建 DSH 会话"
                  className="grid size-6 place-items-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                >
                  <BrainCircuit className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right">新建 DSH 会话</TooltipContent>
            </Tooltip>
          </SidebarGroupLabel>
          {dshOpen ? <SidebarMenu className="min-h-0 flex-1 overflow-y-auto pr-1">
            {dshSessions.slice(0, dshExpanded ? undefined : 5).map((session) => (
              <SidebarMenuItem key={session.id}>
                <SidebarMenuButton asChild isActive={session.id === activeDshSessionId}>
                  <Link href={`/dsh/${session.id}`} aria-label={session.title}>
                    <span className="min-w-0 flex-1 truncate">{session.title || "新 DSH 会话"}</span>
                    <span className="ml-2 shrink-0 text-[10px] text-muted-foreground transition-opacity group-focus-within/menu-item:opacity-0 group-hover/menu-item:opacity-0">{relativeTime(session.updated_at, timezone, locale)}</span>
                  </Link>
                </SidebarMenuButton>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void deleteDshSession(session); }} aria-label="删除 DSH 会话" className="absolute right-8 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded-md text-muted-foreground opacity-0 outline-none transition-[opacity,color,background-color] hover:bg-sidebar-accent hover:text-destructive focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring group-hover/menu-item:opacity-100">
                      <Trash2 className="size-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right">删除会话</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void archiveDshSession(session); }} aria-label="归档 DSH 会话" className="absolute right-2 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded-md text-muted-foreground opacity-0 outline-none transition-[opacity,color,background-color] hover:bg-sidebar-accent hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring group-hover/menu-item:opacity-100">
                      <Archive className="size-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right">归档并保存 Markdown</TooltipContent>
                </Tooltip>
              </SidebarMenuItem>
            ))}
            {!dshSessions.length ? <p className="px-2 py-1.5 text-xs text-muted-foreground">暂无 DSH 会话</p> : null}
            {dshSessions.length > 5 && !dshExpanded ? <SidebarMenuItem className="pt-1"><SidebarMenuButton type="button" size="sm" onClick={() => setDshExpanded(true)} className="text-muted-foreground hover:text-foreground"><span className="min-w-0 flex-1 truncate">显示更多</span><ChevronDown /></SidebarMenuButton></SidebarMenuItem> : null}
            {dshExpanded && dshSessions.length > 5 ? <SidebarMenuItem><SidebarMenuButton type="button" size="sm" onClick={() => setDshExpanded(false)} className="text-muted-foreground hover:text-foreground"><span className="min-w-0 flex-1 truncate">收起</span><ChevronUp /></SidebarMenuButton></SidebarMenuItem> : null}
          </SidebarMenu> : null}
        </SidebarGroup> : null}
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <DesktopUpdateIndicator />
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              isActive={pathname.startsWith("/settings")}
              tooltip={nav("settings")}
            >
              <Link href="/settings">
                <Settings />
                <span>{nav("settings")}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
