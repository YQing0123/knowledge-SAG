"use client";

import * as React from "react";
import { BrainCircuit, RotateCw } from "lucide-react";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api";
import type { DshIntegrationConfig } from "@/lib/types";

export function DshIntegrationSettings() {
  const { refreshCapabilities } = useApp();
  const [config, setConfig] = React.useState<DshIntegrationConfig | null>(null);
  const [gatewayBase, setGatewayBase] = React.useState("");
  const [gatewayToken, setGatewayToken] = React.useState("");
  const [timeout, setTimeoutValue] = React.useState("180");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      const next = await api.getDshIntegration();
      setConfig(next);
      setGatewayBase(next.gateway_base);
      setTimeoutValue(String(next.gateway_timeout_seconds));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "DSH 集成配置加载失败");
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function save(patch?: Partial<DshIntegrationConfig>) {
    setBusy(true);
    try {
      const parsedTimeout = Number(timeout);
      const result = await api.saveDshIntegration({
        enabled: patch?.enabled ?? config?.enabled ?? false,
        gateway_base: gatewayBase.trim() || undefined,
        gateway_timeout_seconds: Number.isFinite(parsedTimeout) ? parsedTimeout : undefined,
        ...(gatewayToken.trim() ? { gateway_token: gatewayToken.trim() } : {}),
      });
      setConfig(result.config);
      setGatewayBase(result.config.gateway_base);
      setTimeoutValue(String(result.config.gateway_timeout_seconds));
      setGatewayToken("");
      await refreshCapabilities();
      window.dispatchEvent(new Event("sag:dsh-integration-changed"));
      toast.success(result.config.enabled ? "DSH 会话集成已启用" : "DSH 会话集成已关闭");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "DSH 集成配置保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="DSH 会话"
      description="通过 DSH SAG 插件网关提供独立会话与深度研究；令牌只保存在 SAG 服务端。"
    >
      <SettingsRow title="启用 DSH 会话" description="关闭后，左侧导航与 DSH 会话记录将不再显示。" layout="inline">
        <Switch
          checked={config?.enabled ?? false}
          disabled={!config || busy}
          onCheckedChange={(enabled) => void save({ enabled })}
          aria-label="启用 DSH 会话"
        />
      </SettingsRow>
      <SettingsRow title="DSH 网关地址" description="DSH 插件暴露的 SAG Agent Gateway 前缀地址。">
        <Input
          value={gatewayBase}
          onChange={(event) => setGatewayBase(event.target.value)}
          placeholder="http://host.docker.internal:3081/ext/sag-agent-gateway"
          disabled={busy}
          inputMode="url"
        />
      </SettingsRow>
      <SettingsRow title="网关服务令牌" description={config?.gateway_token_set ? "已配置。留空并保存不会覆盖现有令牌。" : "需与 DSH 插件的 gatewayToken 保持一致。"}>
        <Input
          value={gatewayToken}
          onChange={(event) => setGatewayToken(event.target.value)}
          type="password"
          autoComplete="new-password"
          placeholder={config?.gateway_token_set ? "已配置，输入新值可替换" : "输入 DSH gatewayToken"}
          disabled={busy}
        />
      </SettingsRow>
      <SettingsRow title="请求超时（秒）" description="SAG 等待 DSH 网关流式响应的最长时间。">
        <Input
          value={timeout}
          onChange={(event) => setTimeoutValue(event.target.value)}
          type="number"
          min={1}
          max={600}
          disabled={busy}
        />
      </SettingsRow>
      <div className="flex justify-end">
        <Button type="button" onClick={() => void save()} disabled={!config || busy}>
          {busy ? <RotateCw className="animate-spin" /> : <BrainCircuit />}
          保存 DSH 配置
        </Button>
      </div>
    </SettingsSection>
  );
}
