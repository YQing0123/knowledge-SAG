"use client";

import * as React from "react";
import { FolderOpen, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export function StorageSettings() {
  const t = useTranslations("StorageSettings");
  const [rootDir, setRootDir] = React.useState("");
  const [logRetentionDays, setLogRetentionDays] = React.useState("30");
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [selecting, setSelecting] = React.useState(false);
  const isDesktop = typeof window !== "undefined" && Boolean(window.sagDesktop?.isDesktop);
  const canChoose = isDesktop && Boolean(window.sagDesktop?.chooseDirectory);

  React.useEffect(() => {
    let active = true;
    void api.getSystemPreferences()
      .then((preferences) => {
        if (active) {
          setRootDir(preferences.root_dir ?? "");
          setLogRetentionDays(String(preferences.processing_log_retention_days ?? 30));
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : t("loadFailed")))
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [t]);

  async function chooseRootDirectory() {
    if (!window.sagDesktop?.chooseDirectory || selecting) return;
    setSelecting(true);
    try {
      const result = await window.sagDesktop.chooseDirectory();
      if (!result.canceled && result.granted && result.path) setRootDir(result.path);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("chooseFailed"));
    } finally {
      setSelecting(false);
    }
  }

  async function saveRootDirectory() {
    if (!rootDir.trim() || saving) return;
    setSaving(true);
    try {
      const saved = await api.saveSystemPreferences({
        root_dir: rootDir.trim(),
        processing_log_retention_days: Math.min(3650, Math.max(1, Number(logRetentionDays) || 30)),
      });
      setRootDir(saved.root_dir);
      setLogRetentionDays(String(saved.processing_log_retention_days));
      toast.success(t("saved"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <SettingsRow title={t("rootTitle")} description={t("rootDescription")}>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={rootDir}
            onChange={(event) => setRootDir(event.target.value)}
            placeholder={t("rootPlaceholder")}
            disabled={loading || saving}
            aria-label={t("rootTitle")}
          />
          {canChoose && (
            <Button type="button" variant="outline" onClick={() => void chooseRootDirectory()} disabled={loading || saving || selecting}>
              <FolderOpen />
              {selecting ? t("choosing") : t("choose")}
            </Button>
          )}
        </div>
      </SettingsRow>
      <SettingsRow title={t("permissionTitle")} description={canChoose ? t("permissionDesktop") : t("permissionDocker")}>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <ShieldCheck className="size-4 shrink-0" />
          <span>{canChoose ? t("permissionGranted") : t("permissionMount")}</span>
        </div>
      </SettingsRow>
      <SettingsRow title={t("logRetentionTitle")} description={t("logRetentionDescription")}>
        <div className="max-w-44">
          <Input
            type="number"
            min={1}
            max={3650}
            value={logRetentionDays}
            onChange={(event) => setLogRetentionDays(event.target.value)}
            disabled={loading || saving}
            aria-label={t("logRetentionTitle")}
          />
        </div>
      </SettingsRow>
      <div className="border-t px-4 py-3 sm:px-5">
        <Button size="sm" onClick={() => void saveRootDirectory()} disabled={loading || saving || !rootDir.trim()}>
          {saving ? t("saving") : t("save")}
        </Button>
      </div>
    </SettingsSection>
  );
}
