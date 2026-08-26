"use client";

import * as React from "react";
import {
  ChevronLeft,
  FolderOpen,
  FolderPlus,
  FolderSync,
  RefreshCw,
  Save,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { FolderSyncConfig, FolderSyncConfigPatch, FolderSyncDirectoryList } from "@/lib/types";
import { cn } from "@/lib/utils";

function toPatch(config: FolderSyncConfig): FolderSyncConfigPatch {
  const directories = config.directories?.length ? config.directories : config.directory ? [config.directory] : [];
  return {
    enabled: config.enabled,
    directory: directories[0] || ".",
    directories,
    mode: config.mode,
    interval_seconds: config.interval_seconds,
    schedule_time: config.schedule_time,
    weekday: config.weekday,
    include_globs: config.include_globs,
    exclude_globs: config.exclude_globs,
    sync_deletions: config.sync_deletions,
    image_enrichment_enabled: config.image_enrichment_enabled,
    image_enrichment_mode: config.image_enrichment_mode,
    image_max_count: config.image_max_count,
    image_max_mb: config.image_max_mb,
  };
}

function commaList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function displayTime(value: string | null, notRun: string): string {
  return value ? new Date(value).toLocaleString() : notRun;
}

export function FolderSyncDialog({ sourceId, onSynced }: { sourceId: string; onSynced: () => void }) {
  const t = useTranslations("FolderSync");
  const [open, setOpen] = React.useState(false);
  const [directoryOpen, setDirectoryOpen] = React.useState(false);
  const [config, setConfig] = React.useState<FolderSyncConfig | null>(null);
  const [draft, setDraft] = React.useState<FolderSyncConfigPatch | null>(null);
  const [listing, setListing] = React.useState<FolderSyncDirectoryList | null>(null);
  const [newDirectory, setNewDirectory] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [running, setRunning] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getFolderSyncConfig(sourceId);
      setConfig(result);
      setDraft(toPatch(result));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [sourceId, t]);

  const browse = React.useCallback(async (path: string) => {
    try {
      setListing(await api.getFolderSyncDirectories(sourceId, path === "." ? "" : path));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("browseFailed"));
    }
  }, [sourceId, t]);

  const update = <K extends keyof FolderSyncConfigPatch>(key: K, value: FolderSyncConfigPatch[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
  };

  async function save(showToast = true): Promise<FolderSyncConfig | null> {
    if (!draft) return null;
    setSaving(true);
    try {
      const result = await api.saveFolderSyncConfig(sourceId, draft);
      setConfig(result);
      setDraft(toPatch(result));
      if (showToast) toast.success(t("saved"));
      return result;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("saveFailed"));
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function run() {
    const saved = await save(false);
    if (!saved) return;
    if (!saved.enabled) {
      toast.error(t("enableFirst"));
      return;
    }
    setRunning(true);
    try {
      await api.runFolderSync(sourceId);
      toast.success(t("queued"));
      window.setTimeout(() => { void load(); onSynced(); }, 800);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("runFailed"));
    } finally {
      setRunning(false);
    }
  }

  function openDirectoryPicker() {
    setDirectoryOpen(true);
    void browse(draft?.directory || ".");
  }

  function selectDirectory() {
    if (!listing) return;
    const directory = listing.current_path;
    if (directory === "." || draft?.directories.includes(directory)) {
      setDirectoryOpen(false);
      return;
    }
    update("directories", [...(draft?.directories ?? []), directory]);
    update("directory", draft?.directories[0] || directory);
    setDirectoryOpen(false);
  }

  function addDirectory() {
    if (!draft) return;
    const raw = newDirectory.trim().replace(/\\/g, "/");
    if (!raw || raw.startsWith("/") || raw.split("/").includes("..")) {
      toast.error(t("directoryInvalid"));
      return;
    }
    const normalized = raw.replace(/^\.\/+/, "").replace(/\/+$/, "") || ".";
    if (draft.directories.includes(normalized)) {
      toast.error(t("directoryDuplicate"));
      return;
    }
    update("directories", [...draft.directories, normalized]);
    update("directory", draft.directories[0] || normalized);
    setNewDirectory("");
  }

  function removeDirectory(directory: string) {
    if (!draft) return;
    const directories = draft.directories.filter((item) => item !== directory);
    update("directories", directories);
    update("directory", directories[0] || "");
  }

  const summary = config?.last_summary ?? {};
  const stageKey = config?.current_stage === "scanning"
    ? "syncStageScanning"
    : config?.current_stage === "image_parsing"
      ? "syncStageImageParsing"
      : config?.current_stage === "image_merging"
        ? "syncStageImageMerging"
        : config?.current_stage === "failed"
          ? "syncStageFailed"
          : null;
  const weekdays = [
    t("weekday0"),
    t("weekday1"),
    t("weekday2"),
    t("weekday3"),
    t("weekday4"),
    t("weekday5"),
    t("weekday6"),
  ];

  React.useEffect(() => {
    if (!open || !config?.current_stage) return;
    const timer = window.setInterval(() => void load(), 2_000);
    return () => window.clearInterval(timer);
  }, [config?.current_stage, load, open]);

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { setOpen(nextOpen); if (nextOpen) void load(); }}>
      <Tooltip>
        <TooltipTrigger asChild>
          <DialogTrigger asChild>
            <Button variant="outline" size="icon" aria-label={t("title")} title={t("title")}>
              <FolderSync className="size-4" />
            </Button>
          </DialogTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">{t("title")}</TooltipContent>
      </Tooltip>
      <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-3xl overflow-y-auto p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{config ? t("description", { root: config.mount_root }) : t("loading")}</DialogDescription>
        </DialogHeader>

        {loading || !config || !draft ? (
          <div className="h-72 animate-pulse rounded-md border bg-muted/20" />
        ) : (
          <div className="space-y-5">
            {!config.mount_available && (
              <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                {t("mountUnavailableBefore")} <code>SAG_SYNC_HOST_PATH</code> {t("mountUnavailableAfter")}
              </div>
            )}
            {stageKey && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-primary/20 bg-primary/5 p-3 text-sm text-foreground">
                <RefreshCw className="size-4 shrink-0 animate-spin text-primary" />
                <span>{t(stageKey)}</span>
                {config.current_file && <span className="w-full truncate text-xs text-muted-foreground sm:ml-6 sm:w-auto">{t("currentFile", { file: config.current_file })}</span>}
              </div>
            )}
            <dl className="grid grid-cols-2 gap-x-5 gap-y-3 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground sm:grid-cols-4">
              <div><dt>{t("lastFinished")}</dt><dd className="mt-1 text-foreground">{displayTime(config.last_finished_at, t("notRun"))}</dd></div>
              <div><dt>{t("newUpdated")}</dt><dd className="mt-1 text-foreground">{summary.created ?? 0} / {summary.updated ?? 0}</dd></div>
              <div><dt>{t("unchanged")}</dt><dd className="mt-1 text-foreground">{summary.unchanged ?? 0}</dd></div>
              <div><dt>{t("deleted")}</dt><dd className="mt-1 text-foreground">{summary.deleted ?? 0}</dd></div>
            </dl>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2 md:col-span-2">
                <Label>{t("directory")}</Label>
                <div className="flex gap-2">
                  <Input value={newDirectory} onChange={(event) => setNewDirectory(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addDirectory(); } }} placeholder={t("directoryPlaceholder")} aria-label={t("directoryPlaceholder")} />
                  <Button type="button" variant="outline" className="shrink-0" onClick={addDirectory}>
                    <FolderPlus className="size-4" />{t("addDirectory")}
                  </Button>
                  <Button type="button" variant="outline" className="shrink-0" onClick={openDirectoryPicker} disabled={!config.mount_available}>
                    <FolderOpen className="size-4" />{t("chooseDirectory")}
                  </Button>
                </div>
                <p className="text-xs leading-5 text-muted-foreground">{t("directoryHelp")}</p>
                <div className="space-y-2 rounded-md border bg-muted/20 p-2">
                  {draft.directories.length === 0 ? <p className="px-1 py-2 text-xs text-muted-foreground">{t("noDirectories")}</p> : draft.directories.map((directory) => (
                    <div key={directory} className="flex min-h-9 items-center gap-2 rounded border bg-background px-2 text-sm">
                      <FolderOpen className="size-4 shrink-0 text-muted-foreground" /><code className="min-w-0 flex-1 truncate">{directory}</code>
                      <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0" aria-label={t("removeDirectory", { directory })} title={t("removeDirectory", { directory })} onClick={() => removeDirectory(directory)}><Trash2 className="size-4" /></Button>
                    </div>
                  ))}
                </div>
              </div>
              <div className="flex min-h-12 items-center justify-between gap-4 rounded-md border bg-background px-3 py-3">
                <div><Label htmlFor="folder-sync-enabled">{t("enabled")}</Label><p className="mt-1 text-xs text-muted-foreground">{t("enabledDescription")}</p></div>
                <Switch id="folder-sync-enabled" className="h-6 w-11" checked={draft.enabled} onCheckedChange={(checked) => update("enabled", checked)} />
              </div>
              <div className="space-y-2">
                <Label>{t("mode")}</Label>
                <Select value={draft.mode} onValueChange={(value) => update("mode", value as FolderSyncConfigPatch["mode"])}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="watch">{t("watch")}</SelectItem>
                    <SelectItem value="interval">{t("interval")}</SelectItem>
                    <SelectItem value="daily">{t("daily")}</SelectItem>
                    <SelectItem value="weekly">{t("weekly")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {draft.mode === "interval" && (
                <div className="space-y-2">
                  <Label htmlFor="folder-sync-interval">{t("intervalSeconds")}</Label>
                  <Input id="folder-sync-interval" type="number" min="60" value={draft.interval_seconds} onChange={(event) => update("interval_seconds", Math.max(60, Number(event.target.value) || 60))} />
                </div>
              )}
              {(draft.mode === "daily" || draft.mode === "weekly") && (
                <div className="space-y-2">
                  <Label htmlFor="folder-sync-time">{t("scheduleTime")}</Label>
                  <Input id="folder-sync-time" type="time" value={draft.schedule_time} onChange={(event) => update("schedule_time", event.target.value || "00:00")} />
                  <p className="text-xs text-muted-foreground">{t("scheduleTimezone", { timezone: config.timezone })}</p>
                </div>
              )}
              {draft.mode === "weekly" && (
                <div className="space-y-2">
                  <Label>{t("weekday")}</Label>
                  <Select value={String(draft.weekday)} onValueChange={(value) => update("weekday", Number(value))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{weekdays.map((weekday, index) => <SelectItem key={weekday} value={String(index)}>{weekday}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              )}
              <div className={cn("space-y-2", draft.mode === "watch" && "md:col-span-2")}>
                <Label htmlFor="folder-sync-include">{t("include")}</Label>
                <Input id="folder-sync-include" value={draft.include_globs.join(", ")} onChange={(event) => update("include_globs", commaList(event.target.value))} />
              </div>
              <div className="space-y-2 md:col-span-2">
                <Label htmlFor="folder-sync-exclude">{t("exclude")}</Label>
                <Input id="folder-sync-exclude" value={draft.exclude_globs.join(", ")} placeholder={t("excludePlaceholder")} onChange={(event) => update("exclude_globs", commaList(event.target.value))} />
              </div>
            </div>
            <div className="flex items-center justify-between gap-4 rounded-md border bg-background px-3 py-3">
              <div><Label htmlFor="folder-sync-delete">{t("delete")}</Label><p className="mt-1 text-xs text-muted-foreground">{t("deleteDescription")}</p></div>
              <Switch id="folder-sync-delete" className="h-6 w-11" checked={draft.sync_deletions} onCheckedChange={(checked) => update("sync_deletions", checked)} />
            </div>
            <div className="space-y-4 rounded-md border bg-background p-3">
              <div className="flex items-center justify-between gap-4">
                <div><Label htmlFor="folder-sync-images">{t("imageEnrichment")}</Label><p className="mt-1 text-xs text-muted-foreground">{t("imageEnrichmentDescription")}</p></div>
                <Switch id="folder-sync-images" className="h-6 w-11" checked={draft.image_enrichment_enabled} disabled={!config.image_enrichment_available} onCheckedChange={(checked) => update("image_enrichment_enabled", checked)} />
              </div>
              {draft.image_enrichment_enabled && <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-2"><Label>{t("imageMode")}</Label><Select value={draft.image_enrichment_mode} onValueChange={(value) => update("image_enrichment_mode", value as "vlm" | "ocr")}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="vlm">{t("imageModeVlm")}</SelectItem><SelectItem value="ocr">{t("imageModeOcr")}</SelectItem></SelectContent></Select></div>
                <div className="space-y-2"><Label htmlFor="folder-sync-image-count">{t("imageMaxCount")}</Label><Input id="folder-sync-image-count" type="number" min="1" max="50" value={draft.image_max_count} onChange={(event) => update("image_max_count", Math.min(50, Math.max(1, Number(event.target.value) || 1)))} /></div>
                <div className="space-y-2"><Label htmlFor="folder-sync-image-mb">{t("imageMaxMb")}</Label><Input id="folder-sync-image-mb" type="number" min="1" max="50" value={draft.image_max_mb} onChange={(event) => update("image_max_mb", Math.min(50, Math.max(1, Number(event.target.value) || 1)))} /></div>
              </div>}
              {draft.image_enrichment_enabled && !config.image_enrichment_available && <p className="text-xs text-destructive">{t("imageMineruUnavailable")}</p>}
            </div>
            {config.last_error && <p className="text-sm text-destructive">{t("lastError", { error: config.last_error })}</p>}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => void save()} disabled={saving || loading}><Save className="size-4" />{saving ? t("saving") : t("save")}</Button>
          <Button onClick={() => void run()} disabled={running || saving || loading || !config?.mount_available}><RefreshCw className={cn("size-4", running && "animate-spin")} />{running ? t("running") : t("run")}</Button>
        </DialogFooter>
      </DialogContent>

      <Dialog open={directoryOpen} onOpenChange={setDirectoryOpen}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-xl overflow-y-auto p-4 sm:p-6">
          <DialogHeader>
            <DialogTitle>{t("chooseDirectory")}</DialogTitle>
            <DialogDescription>{listing ? t("browseDescription", { root: listing.mount_root }) : t("loading")}</DialogDescription>
          </DialogHeader>
          {listing && listing.mount_available ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/20 p-3 text-sm"><code className="min-w-0 truncate">{listing.current_path}</code>{listing.parent_path !== null && <Button type="button" variant="outline" size="icon" aria-label={t("upDirectory")} onClick={() => void browse(listing.parent_path!)}><ChevronLeft className="size-4" /></Button>}</div>
              <div className="max-h-72 overflow-y-auto rounded-md border">
                {listing.directories.length === 0 ? <p className="p-4 text-sm text-muted-foreground">{t("emptyDirectory")}</p> : listing.directories.map((directory) => <button key={directory.path} type="button" className="flex min-h-11 w-full items-center gap-2 border-b px-3 text-left text-sm last:border-b-0 hover:bg-muted" onClick={() => void browse(directory.path)}><FolderOpen className="size-4 shrink-0 text-muted-foreground" /><span className="truncate">{directory.name}</span></button>)}
              </div>
            </div>
          ) : <p className="text-sm text-destructive">{t("mountUnavailableBefore")} <code>SAG_SYNC_HOST_PATH</code> {t("mountUnavailableAfter")}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDirectoryOpen(false)}>{t("cancel")}</Button>
            <Button type="button" onClick={selectDirectory} disabled={!listing?.mount_available}>{t("useThisDirectory")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}
