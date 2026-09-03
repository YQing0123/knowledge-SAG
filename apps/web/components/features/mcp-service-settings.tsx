"use client";

import * as React from "react";
import { Database, Globe2, KeyRound, LockKeyhole, Plus, RotateCw, Terminal, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { CodeBlock } from "@/components/features/code-block";
import { CopyButton } from "@/components/features/copy-button";
import { McpToolList } from "@/components/features/mcp-tool-list";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError } from "@/lib/api";
import { SAG_KNOWLEDGE_MCP_SERVER_KEY } from "@/lib/mcp-server-key";
import type { KnowledgeMcpDescriptor, McpApiKey } from "@/lib/types";

function httpConfig(descriptor: KnowledgeMcpDescriptor, token?: string | null) {
  const headers = { ...descriptor.http.headers };
  headers.Authorization = `Bearer ${token || "<MCP_API_KEY>"}`;
  const transport = descriptor.http.transport.replace("-", "_");
  return {
    mcpServers: {
      [SAG_KNOWLEDGE_MCP_SERVER_KEY]: {
        type: "http",
        transport,
        url: descriptor.http.url,
        headers,
      },
    },
  };
}

function stdioConfig(descriptor: KnowledgeMcpDescriptor) {
  return {
    mcpServers: {
      [SAG_KNOWLEDGE_MCP_SERVER_KEY]: {
        command: descriptor.stdio.command,
        args: descriptor.stdio.args,
      },
    },
  };
}

export function McpServiceSettings() {
  const t = useTranslations("McpService");
  const [descriptor, setDescriptor] = React.useState<KnowledgeMcpDescriptor | null>(null);
  const [keys, setKeys] = React.useState<McpApiKey[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<"http" | "stdio">("http");
  const [keyName, setKeyName] = React.useState("");
  const [newKey, setNewKey] = React.useState<string | null>(null);
  const [keyBusy, setKeyBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [nextDescriptor, nextKeys] = await Promise.all([
        api.knowledgeMcp(),
        api.listMcpApiKeys(),
      ]);
      setDescriptor(nextDescriptor);
      setKeys(nextKeys);
    } catch (loadError) {
      setError(loadError instanceof ApiError ? loadError.message : t("loadFailed"));
    }
  }, [t]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const snippets = React.useMemo(() => {
    if (!descriptor) return null;
    const previewHeaders = {
      ...descriptor.http.headers,
      Authorization: "Bearer <MCP_API_KEY>",
    };
    const transport = descriptor.http.transport.replace("-", "_");
    return {
      httpPreview: JSON.stringify(
        {
          mcpServers: {
            [SAG_KNOWLEDGE_MCP_SERVER_KEY]: {
              type: "http",
              transport,
              url: descriptor.http.url,
              headers: previewHeaders,
            },
          },
        },
        null,
        2,
      ),
      httpCopy: JSON.stringify(httpConfig(descriptor, newKey), null, 2),
      stdio: JSON.stringify(stdioConfig(descriptor), null, 2),
    };
  }, [descriptor, newKey]);

  if (error) {
    return (
      <SettingsSection title={t("title")} description={t("description")}>
        <SettingsRow title={t("serviceConfig")}>
          <Alert variant="destructive">
            <AlertTitle>{t("loadErrorTitle")}</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
              <span>{error}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RotateCw />
                {t("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        </SettingsRow>
      </SettingsSection>
    );
  }

  if (!descriptor || !snippets) {
    return (
      <SettingsSection title={t("title")} description={t("description")}>
        <SettingsRow title={t("scope")}>
          <div className="flex gap-2">
            <Skeleton className="h-7 w-28" />
            <Skeleton className="h-7 w-20" />
          </div>
        </SettingsRow>
        <SettingsRow title={t("connectionConfig")}>
          <div className="grid gap-3">
            <Skeleton className="h-8 w-44" />
            <Skeleton className="h-52 w-full" />
          </div>
        </SettingsRow>
      </SettingsSection>
    );
  }

  const preview = mode === "http" ? snippets.httpPreview : snippets.stdio;
  const copyValue = mode === "http" ? snippets.httpCopy : snippets.stdio;
  const note = mode === "http" ? descriptor.http.note : descriptor.stdio.note;

  async function createKey() {
    if (!keyName.trim()) return;
    setKeyBusy(true);
    try {
      const created = await api.createMcpApiKey(keyName.trim());
      setKeys((current) => [created, ...(current ?? [])]);
      setNewKey(created.key);
      setKeyName("");
      toast.success(t("keyCreated"));
    } catch (createError) {
      toast.error(createError instanceof ApiError ? createError.message : t("keyCreateFailed"));
    } finally {
      setKeyBusy(false);
    }
  }

  async function revokeKey(key: McpApiKey) {
    setKeyBusy(true);
    try {
      const revoked = await api.revokeMcpApiKey(key.id);
      setKeys((current) => (current ?? []).map((item) => item.id === revoked.id ? revoked : item));
      if (newKey && key.key_prefix && newKey.startsWith(key.key_prefix)) setNewKey(null);
      toast.success(t("keyRevoked"));
    } catch (revokeError) {
      toast.error(revokeError instanceof ApiError ? revokeError.message : t("keyRevokeFailed"));
    } finally {
      setKeyBusy(false);
    }
  }

  return (
    <SettingsSection title={t("title")} description={t("fullDescription")}>
      <SettingsRow
        title={t("scope")}
        description={t("scopeDescription")}
        layout="inline"
      >
        <div className="flex flex-wrap justify-start gap-2 sm:justify-end">
          <Badge variant="secondary" className="gap-1.5">
            <Database />
            {t("allKnowledge")}
          </Badge>
          <Badge variant="outline">{t("sourceCount", { count: descriptor.source_count })}</Badge>
          <Badge variant="outline">{t("toolCount", { count: descriptor.tools.length })}</Badge>
        </div>
      </SettingsRow>

      <SettingsRow title={t("apiKeys")} description={t("apiKeysDescription")}>
        <div className="grid gap-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={keyName}
              onChange={(event) => setKeyName(event.target.value)}
              placeholder={t("keyNamePlaceholder")}
              aria-label={t("keyName")}
              onKeyDown={(event) => { if (event.key === "Enter") void createKey(); }}
            />
            <Button type="button" onClick={() => void createKey()} disabled={!keyName.trim() || keyBusy}>
              <Plus />
              {t("generateKey")}
            </Button>
          </div>
          {newKey ? (
            <Alert>
              <KeyRound />
              <AlertTitle>{t("keyShownOnce")}</AlertTitle>
              <AlertDescription className="flex flex-wrap items-center gap-2">
                <code className="break-all text-xs">{newKey}</code>
                <CopyButton text={newKey} label={t("apiKey")} />
              </AlertDescription>
            </Alert>
          ) : null}
          {(keys ?? []).length ? (
            <div className="grid gap-2">
              {(keys ?? []).map((key) => (
                <div key={key.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">
                    <span className="font-medium">{key.name}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{key.key_prefix}...</span>
                    {!key.active ? <Badge variant="destructive" className="ml-2">{t("revoked")}</Badge> : null}
                  </span>
                  {key.active ? (
                    <Button type="button" variant="ghost" size="sm" disabled={keyBusy} onClick={() => void revokeKey(key)}>
                      <Trash2 />
                      {t("revoke")}
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("noKeys")}</p>
          )}
        </div>
      </SettingsRow>

      <SettingsRow title={t("connectionConfig")} description={t("connectionDescription")}>
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={mode}
              onValueChange={(value) => value && setMode(value as typeof mode)}
              aria-label={t("connectionAria")}
            >
              <ToggleGroupItem value="http">
                <Globe2 />
                HTTP
              </ToggleGroupItem>
              <ToggleGroupItem value="stdio">
                <Terminal />
                {t("localCommand")}
              </ToggleGroupItem>
            </ToggleGroup>
            <CopyButton text={copyValue} label={t("mcpConfig")} />
          </div>
          <CodeBlock>{preview}</CodeBlock>
          <div className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            {mode === "http" ? (
              <LockKeyhole className="mt-0.5 size-3.5 shrink-0" />
            ) : (
              <Terminal className="mt-0.5 size-3.5 shrink-0" />
            )}
            <span>
              {mode === "http"
                ? t("tokenNote", { note })
                : note}
            </span>
          </div>
        </div>
      </SettingsRow>

      <SettingsRow title={t("availableTools")} description={t("toolsDescription")}>
        <McpToolList tools={descriptor.tool_details} />
      </SettingsRow>
    </SettingsSection>
  );
}
