"use client";

import * as React from "react";
import { FolderArchive, LibraryBig, LogOut, Save } from "lucide-react";
import { useTranslations } from "next-intl";

import { useApp } from "@/components/features/app-shell";
import { ArchivedThreadsCard } from "@/components/features/archived-threads-card";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export function AccountSettings() {
  const t = useTranslations("AccountSettings");
  const { user, logout } = useApp();
  const [archiveDir, setArchiveDir] = React.useState("");
  const [ingestDir, setIngestDir] = React.useState("");
  const [savingFolders, setSavingFolders] = React.useState(false);
  const initial =
    user?.name.trim().slice(0, 1).toUpperCase() ||
    user?.email.trim().slice(0, 1).toUpperCase() ||
    "?";

  React.useEffect(() => {
    void api.getSystemPreferences().then((preferences) => {
      setArchiveDir(preferences.conversation_archive_dir ?? "");
      setIngestDir(preferences.conversation_ingest_dir ?? "");
    });
  }, []);

  async function saveConversationFolders() {
    setSavingFolders(true);
    try {
      const saved = await api.saveSystemPreferences({
        conversation_archive_dir: archiveDir,
        conversation_ingest_dir: ingestDir,
      });
      setArchiveDir(saved.conversation_archive_dir);
      setIngestDir(saved.conversation_ingest_dir);
    } finally {
      setSavingFolders(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("identityTitle")} description={t("identityDescription")}>
        <div className="flex items-center justify-between gap-4 p-4 sm:p-5">
          <div className="flex min-w-0 items-center gap-3">
            <Avatar className="size-10">
              <AvatarFallback className="text-sm">{initial}</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {user?.name || t("nameMissing")}
              </div>
              <div className="mt-0.5 truncate text-sm text-muted-foreground">
                {user?.email || t("emailMissing")}
              </div>
            </div>
          </div>
          <Badge variant="success" className="shrink-0">
            {t("local")}
          </Badge>
        </div>
        <SettingsRow
          title={t("signOutTitle")}
          description={t("signOutDescription")}
          layout="inline"
        >
          <Button variant="outline" size="sm" onClick={logout}>
            <LogOut />
            {t("signOut")}
          </Button>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        title="会话文件"
        description="DSH 会话归档与回答入库会保存为 Markdown。目录必须位于已挂载的知识库根目录内。"
      >
        <SettingsRow title="归档文件夹" description="归档会话时保留消息、引用和过程轨迹。">
          <div className="flex gap-2">
            <FolderArchive className="mt-2.5 size-4 shrink-0 text-muted-foreground" />
            <Input value={archiveDir} onChange={(event) => setArchiveDir(event.target.value)} placeholder="/sync/知识层/会话归档" />
          </div>
        </SettingsRow>
        <SettingsRow title="回答入库文件夹" description="在每条 DSH 回答下点击入库时另存完整 Markdown。">
          <div className="flex gap-2">
            <LibraryBig className="mt-2.5 size-4 shrink-0 text-muted-foreground" />
            <Input value={ingestDir} onChange={(event) => setIngestDir(event.target.value)} placeholder="/sync/知识层/会话入库" />
          </div>
        </SettingsRow>
        <div className="border-t px-4 py-3 sm:px-5">
          <Button size="sm" onClick={() => void saveConversationFolders()} disabled={savingFolders}>
            <Save />
            {savingFolders ? "保存中" : "保存文件夹设置"}
          </Button>
        </div>
      </SettingsSection>

      <ArchivedThreadsCard />
    </div>
  );
}
