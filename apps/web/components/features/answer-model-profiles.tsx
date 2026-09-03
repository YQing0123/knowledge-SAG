"use client";

import * as React from "react";
import { Download, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { AnswerModelItem, AnswerModelProfile, AnswerModelProtocol } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Draft = {
  provider_id: string;
  display_name: string;
  base_url: string;
  protocol: AnswerModelProtocol;
  api_key: string;
  models: AnswerModelItem[];
  is_default: boolean;
};

const emptyDraft = (): Draft => ({
  provider_id: "",
  display_name: "",
  base_url: "",
  protocol: "openai-compatible",
  api_key: "",
  models: [],
  is_default: false,
});

export function AnswerModelProfiles() {
  const [profiles, setProfiles] = React.useState<AnswerModelProfile[]>([]);
  const [editing, setEditing] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [discovering, setDiscovering] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try { setProfiles(await api.getAnswerModelProfiles()); }
    catch (error) { toast.error(error instanceof Error ? error.message : "加载回答模型失败"); }
    finally { setLoading(false); }
  }, []);
  React.useEffect(() => { void load(); }, [load]);

  function openNew() { setEditing("new"); setDraft(emptyDraft()); }
  function openEdit(profile: AnswerModelProfile) {
    setEditing(profile.id);
    setDraft({ ...profile, api_key: "" });
  }
  function closeEditor() { setEditing(null); setDraft(null); }

  async function save() {
    if (!draft) return;
    if (!draft.provider_id.trim() || !draft.display_name.trim() || !draft.base_url.trim()) {
      toast.error("请填写提供方 ID、显示名称和 API 地址"); return;
    }
    if (editing === "new" && !draft.api_key.trim()) { toast.error("请输入 API Key"); return; }
    setSaving(true);
    try {
      const body = {
        provider_id: draft.provider_id.trim(), display_name: draft.display_name.trim(),
        base_url: draft.base_url.trim(), protocol: draft.protocol, models: draft.models,
        ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
        is_default: draft.is_default,
      };
      if (editing === "new") await api.createAnswerModelProfile(body as Parameters<typeof api.createAnswerModelProfile>[0]);
      else await api.updateAnswerModelProfile(editing!, body);
      toast.success("回答模型已保存"); closeEditor(); await load();
    } catch (error) { toast.error(error instanceof Error ? error.message : "保存失败"); }
    finally { setSaving(false); }
  }

  async function discover() {
    if (editing === "new") {
      if (!draft?.base_url.trim() || !draft.api_key.trim()) {
        toast.error("请先填写 API 地址和 API Key");
        return;
      }
    } else if (!editing) {
      toast.error("请先打开提供方编辑器");
      return;
    }
    setDiscovering(true);
    try {
      const result = editing === "new"
        ? await api.discoverAnswerModels({ base_url: draft!.base_url, api_key: draft!.api_key, protocol: draft!.protocol })
        : await api.discoverAnswerModelProfile(editing!);
      setDraft((current) => current ? { ...current, models: result.models } : current);
      toast.success(`已获取 ${result.count} 个模型`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "获取模型目录失败"); }
    finally { setDiscovering(false); }
  }

  async function remove(profile: AnswerModelProfile) {
    if (!window.confirm(`删除提供方“${profile.display_name}”？`)) return;
    try { await api.deleteAnswerModelProfile(profile.id); await load(); toast.success("已删除"); }
    catch (error) { toast.error(error instanceof Error ? error.message : "删除失败"); }
  }

  function updateModel(index: number, patch: Partial<AnswerModelItem>) {
    setDraft((current) => current ? { ...current, models: current.models.map((item, i) => i === index ? { ...item, ...patch } : item) } : current);
  }

  return (
    <section className="rounded-xl border bg-card p-5 shadow-soft">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg text-foreground">回答模型</h2>
          <p className="mt-1 text-sm text-muted-foreground">回答模型与文档抽取模型分开管理。仅支持 OpenAI 协议兼容的自定义提供方。</p>
        </div>
        <Button type="button" size="sm" onClick={openNew}><Plus className="mr-1 size-4" />添加提供方</Button>
      </div>

      <div className="mt-4 space-y-3">
        {loading && <p className="text-sm text-muted-foreground">正在加载...</p>}
        {!loading && profiles.length === 0 && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">尚未配置回答模型。</p>}
        {profiles.map((profile) => (
          <div key={profile.id} className="rounded-lg border p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2"><span className="font-medium">{profile.display_name}</span>{profile.is_default && <span className="rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground">默认</span>}<span className={profile.api_key_set ? "size-2 rounded-full bg-emerald-500" : "size-2 rounded-full bg-muted-foreground"} /></div>
                <div className="mt-1 truncate text-xs text-muted-foreground">{profile.provider_id} · {profile.protocol} · {profile.models.length} 个模型</div>
              </div>
              <div className="flex shrink-0 items-center gap-1"><Button variant="ghost" size="icon" onClick={() => openEdit(profile)} aria-label="编辑"><Pencil className="size-4" /></Button><Button variant="ghost" size="icon" className="text-destructive" onClick={() => void remove(profile)} aria-label="删除"><Trash2 className="size-4" /></Button></div>
            </div>
          </div>
        ))}
      </div>

      {draft && (
        <div className="mt-5 rounded-xl border bg-muted/20 p-4">
          <div className="mb-4 flex items-center justify-between"><h3 className="font-medium">{editing === "new" ? "添加自定义提供方" : "编辑提供方"}</h3><Button variant="ghost" size="icon" onClick={closeEditor} aria-label="关闭"><X className="size-4" /></Button></div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1.5 text-sm"><span>Provider ID</span><Input value={draft.provider_id} onChange={(e) => setDraft({ ...draft, provider_id: e.target.value })} placeholder="siliconflow" /></label>
            <label className="space-y-1.5 text-sm"><span>显示名称</span><Input value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} placeholder="硅基流动" /></label>
          </div>
          <label className="mt-3 block space-y-1.5 text-sm"><span>API 地址</span><Input value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} placeholder="https://api.siliconflow.cn/v1" /></label>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="space-y-1.5 text-sm"><span>API 协议</span><select value={draft.protocol} onChange={(e) => setDraft({ ...draft, protocol: e.target.value as AnswerModelProtocol })} className="h-10 w-full rounded-md border bg-background px-3 text-sm"><option value="openai-compatible">openai-compatible</option><option value="openai-responses">openai-responses</option></select></label>
            <label className="space-y-1.5 text-sm"><span>API 密钥 {editing !== "new" && "（留空保持不变）"}</span><Input type="password" value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} placeholder="sk-..." /></label>
          </div>
          <div className="mt-5 flex items-center justify-between border-t pt-4"><div><h4 className="font-medium">模型目录</h4><p className="text-xs text-muted-foreground">每个模型可单独设置上下文窗口和最大输出 token。</p></div><Button type="button" variant="outline" size="sm" disabled={discovering} onClick={() => void discover()}><Download className="mr-1 size-4" />{discovering ? "获取中..." : "获取可用模型"}</Button></div>
          <div className="mt-3 space-y-2">
            {draft.models.map((model, index) => <div key={`${model.id}-${index}`} className="grid gap-2 rounded-lg border bg-background p-3 md:grid-cols-[1.4fr_1fr_0.8fr_0.8fr_auto]"><Input value={model.id} onChange={(e) => updateModel(index, { id: e.target.value })} placeholder="模型 ID" /><Input value={model.display_name} onChange={(e) => updateModel(index, { display_name: e.target.value })} placeholder="显示名称" /><Input type="number" value={model.context_window} onChange={(e) => updateModel(index, { context_window: Number(e.target.value) })} placeholder="上下文" /><Input type="number" value={model.max_output_tokens} onChange={(e) => updateModel(index, { max_output_tokens: Number(e.target.value) })} placeholder="最大输出" /><Button type="button" variant="ghost" size="icon" className="text-destructive" onClick={() => setDraft({ ...draft, models: draft.models.filter((_, i) => i !== index) })} aria-label="删除模型"><Trash2 className="size-4" /></Button></div>)}
            <Button type="button" variant="outline" size="sm" onClick={() => setDraft({ ...draft, models: [...draft.models, { id: "", display_name: "", context_window: 128000, max_output_tokens: 8192 }] })}><Plus className="mr-1 size-4" />添加模型</Button>
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.is_default} onChange={(e) => setDraft({ ...draft, is_default: e.target.checked })} />设为默认回答模型</label>
          <div className="mt-5 flex justify-end gap-2"><Button type="button" variant="outline" onClick={closeEditor}>取消</Button><Button type="button" disabled={saving} onClick={() => void save()}><Save className="mr-1 size-4" />{saving ? "保存中..." : "保存"}</Button></div>
        </div>
      )}
    </section>
  );
}
