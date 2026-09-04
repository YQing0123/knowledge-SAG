"use client";

import * as React from "react";
import { AlertTriangle, FileText, ScrollText } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import type { ProcessingLogEntry } from "@/lib/types";
import { relativeTime } from "@/lib/format";

type Filter = "all" | "error";

export function ProcessingLogDialog({ sourceId }: { sourceId: string }) {
  const t = useTranslations("ProcessingLogs");
  const [open, setOpen] = React.useState(false);
  const [filter, setFilter] = React.useState<Filter>("all");
  const [logs, setLogs] = React.useState<ProcessingLogEntry[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setLogs(await api.listProcessingLogs(sourceId, filter === "error" ? { level: "error" } : {}));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [filter, sourceId, t]);

  React.useEffect(() => {
    if (open) void load();
  }, [load, open]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="icon" aria-label={t("title")} title={t("title")}>
          <ScrollText className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-4xl flex-col p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between gap-3">
          <Select value={filter} onValueChange={(value) => setFilter(value as Filter)}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("all")}</SelectItem>
              <SelectItem value="error">{t("errors")}</SelectItem>
            </SelectContent>
          </Select>
          <Button type="button" variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            {t("refresh")}
          </Button>
        </div>
        <div className="min-h-0 overflow-y-auto rounded-md border">
          {loading ? (
            <p className="p-5 text-sm text-muted-foreground">{t("loading")}</p>
          ) : error ? (
            <p className="p-5 text-sm text-destructive">{error}</p>
          ) : logs.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">{t("empty")}</p>
          ) : (
            <div className="divide-y">
              {logs.map((entry) => (
                <div key={entry.id} className="grid gap-1 p-3 sm:grid-cols-[10rem_minmax(0,1fr)_auto] sm:items-start">
                  <span className="text-xs text-muted-foreground" title={entry.created_at}>{relativeTime(entry.created_at)}</span>
                  <div className="min-w-0">
                    <div className="flex min-w-0 items-center gap-2 text-sm">
                      {entry.level === "error" ? <AlertTriangle className="size-4 shrink-0 text-destructive" /> : <FileText className="size-4 shrink-0 text-muted-foreground" />}
                      <span className="truncate font-medium">{entry.filename ?? t("sourceLevel")}</span>
                    </div>
                    <p className="mt-1 break-words text-xs text-muted-foreground">{entry.message}</p>
                  </div>
                  <span className={entry.level === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>{entry.stage}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
