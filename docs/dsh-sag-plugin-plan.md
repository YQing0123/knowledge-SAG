# SAG 知识库 × DSH Web 集成插件 —— 可行性分析与实现方案

> 分析日期：2026-08-25 ｜ 结论：**整体可行，全部 5 项需求可落地**，且 DSH 生态已有可直接复用的现成模式（任务板插件、MCP 设置插件、@文件提及插件）。

---

## 0. 结论速览

| 需求 | 可行性 | 实现路径 | 风险等级 |
| --- | --- | --- | --- |
| 1. 菜单首页嵌入整个 SAG 配置模块 | ✅ 可行 | `sidebar.footer.action` 槽位 + `shell.overlay` 全屏面板 + `<iframe>` | 低 |
| 2. 设置页新增「SAG 知识库插件设置」 | ✅ 可行 | `settings.section` 槽位注册（参考 `dsh-plugin-setting-mcp`） | 低 |
| 3. 首页顶部标签页：知识库搜索 / 知识库管理 | ✅ 可行 | overlay 面板内 React tabs + 两个 iframe 路由切换 | 低 |
| 4. 全部 iframe 嵌套、不额外开发 | ✅ 可行 | SAG web 无 X-Frame-Options / CSP 帧限制，可被 iframe | 低 |
| 5. DSH 会话技能替代 SAG 问答（/检索 + @选库） | ✅ 可行 | 优先：MCP 挂载（`mcp__sag__*` 工具自动进 agent）+ 技能/命令/@提及；备选：REST 直连工具 | 中 |

唯一需要设计注意的硬约束：**iframe 的主机名必须与 DSH GUI 当前主机名一致**（见 §3.2），插件自动推导即可解决。

---

## 1. SAG 侧事实核查（已读源码确认）

### 1.1 部署现状
- Docker compose 正在运行：`sag-web-1`（127.0.0.1:3000，Next.js 15）+ `sag-api-1`（127.0.0.1:8000，FastAPI），均 healthy。
- 数据：SQLite + LanceDB，卷 `sagdata`；模型配置通过 Settings 页运行时调整（`SAG_LOCK_LLM_CONFIG` 默认 false）。

### 1.2 Web 前端可 iframe 的页面（routes 确认）
| DSH 侧需求 | SAG 页面 URL |
| --- | --- |
| 知识库搜索 | `http://<host>:3000/search` |
| 知识库管理 | `http://<host>:3000/knowledge` / `.../knowledge/[id]` |
| 知识库配置设置 | `http://<host>:3000/settings?tab=knowledge` |
| 模型设置 | `http://<host>:3000/settings?tab=model` |
| 集成设置（MCP/外部库） | `http://<host>:3000/settings?tab=integrations` |
| 图谱设置 | `http://<host>:3000/settings?tab=graph` |
| 诊断日志 | `http://<host>:3000/settings?tab=diagnostics` |

- `middleware.ts`：无 X-Frame-Options、无 CSP frame-ancestors —— **iframe 嵌入没有帧级障碍**。
- 登录：`/login` 页输入名字 → `POST /api/v1/auth/login` → JWT 以 `document.cookie`（`sag_token_<port>`，SameSite=Lax，7 天）写入，随后跳 `/chat`。`middleware` 按 cookie 放行。

### 1.3 后端 API（供技能/MCP 使用的检索面）
| 端点 | 用途 |
| --- | --- |
| `POST /auth/login` `{name}` | 换取 JWT（本地身份） |
| `GET/POST /sources`、`PATCH/DELETE /sources/{id}` | 知识库（信源）生命周期 |
| `POST /search` | 全局检索，body 支持 `query` + `strategy`(vector/multi) + `top_k` + **`source_ids[]`**（@多库选择的关键参数） |
| `POST /sources/{id}/search` | 单库检索 |
| `/sources/{id}/entities`、`/sources/{id}/graph` | 图谱数据 |
| `POST /openai/{agent_id}/chat/completions` | SAG 自带 OpenAI 兼容问答（**需求5要替换的对象**） |
| `GET /mcp/`（Streamable HTTP） | 知识库 MCP 服务 |

### 1.4 SAG MCP 工具集（需求 5 的现成检索工具）
`list_sources` / `search(query, top_k, source_id)` / `get_entity` / `list_documents` / `outline` / `grep` / `read` / `get_chunk`
—— 全部支持按 `source_id` 限定范围，正是「@选库 → 限定检索」的天然映射。

### 1.5 SAG 问答能力（需求 5 要替代的）
SAG 内置 agent 工具：`search_context`（检索+[n]编号证据）、`get_entity`、`get_time`、`web_search`、`open_web_page`；用 SAG 配置的 LLM（默认 302AI qwen3.6-flash）做生成。**需求 5 的本质：保住 SAG 的检索引擎（event-entity 索引 + 动态超边），把"提问理解 + 答案生成"换成 DSH 当前会话模型**，并让 @ 选择多库、/ 快速唤起。

---

## 2. DSH 侧事实核查（已读源码/已装配插件确认）

### 2.1 插件体系（dsh-super-injector + cordis bundle）
已装配插件清单中直接可参考的现成实现：
- `@shengsheng/dsh-taskboard` —— **任务板**：侧边栏按钮(`sidebar.footer.action`) + 全屏 overlay 页(`shell.overlay`) + host 工具 + 随包技能 + 本地 SQLite。**这就是 SAG 插件的形态模板**。
- `@opendsh/dsh-plugin-setting-mcp` —— **设置页 MCP 管理**：`settings.section` 槽位注入设置页 + `ctx.mcp` host 服务管理 `dsh-mcp-client` loader 条目（热生效 + 持久化到 cordis.patch.yml）。
- `dsh-at-file` —— **@ 提及**：消息预处理钩子解析 `@token` → 注入结构化引用内容。@选库直接复用此模式。
- `dsh-better-sidebar` —— 侧边栏改造插件（证明侧边栏可注入）。
- 同时会话内 `dev_*`（dsh-super-injector）支持**免重启运行时注入/热重载/卸载即净**。

### 2.2 DSH UI 槽位地图（注册点全集）
| 槽位 | 类型 | 用途（对应需求） |
| --- | --- | --- |
| `sidebar.footer.action` | list | 侧边栏底部动作：SAG 菜单按钮（`order` 控制排序，设 `order:-1` 即在任务板按钮上方） |
| `shell.overlay` | list | 全屏浮层面板：SAG 主界面（标签页 + iframe） |
| `settings.section` | list | 设置页分区：SAG 插件设置（`settings?tab` 切换 SAG 各设置页 iframe） |
| `settings.plugins.tab` | list | 设置页「插件」分区内的 tab |
| `conversation.composer` | chain | 输入框链（如需在输入框加 @ 提示/按钮） |
| （运行时可自定义新槽位） | — | 插件可 declare 自己的槽位 |

### 2.3 MCP 客户端（需求 5 主路径）
`@deepseek-ai/dsh-mcp-client` 是 DSH 一等基础设施：配置 `transport: "streamable-http"` + `url` + `headers` 即可连 SAG MCP，且**挂载后工具自动注册进 `ctx.tools`，命名为 `mcp__<serverName>__<rawName>`**（如 `mcp__sag__search`），直接成为会话 agent 原生可调用工具。`dsh-plugin-setting-mcp` 已证明该 loader 条目可热生效并持久化。

### 2.4 命令与技能
- `commands` 服务：插件注册斜杠命令（`/sag ...`），处理器拿 raw text 执行（可触发检索、切 iframe 页面等）。
- 技能机制：随包 `skills/sag-knowledge/SKILL.md`（任务板就是这么带技能的），指导模型何时调用 `mcp__sag__*`、如何解析 @选库与 /命令。
- @ 提及：`dsh-at-file` 的 message 预处理钩子模式可复刻为「@知识库名 → 解析为 source_id 列表注入引用」。

---

## 3. 可行性分项论证

### 3.1 需求 1–4（iframe 嵌套 UI）—— 低风险可行
- 所有 SAG 页面无帧级封锁 header；`shell.overlay` 是**additive**槽位（"a fresh id is added beside the shipped entries"），新面板不破坏现有 UI（任务板 overlay 同款）。
- overlay 默认 click-through，iframe 内部需要 pointer-events 回流（occupant 自己控制，任务板已示范如何在自己的页面里接管交互）。
- 设置分区用 `settings.section`（list + `order`）：在 DSH 设置页新增「SAG 知识库」分区，内含 5 个 tab（知识库/模型/集成/图谱/诊断），每个 tab 一个 SAG `/settings?tab=...` iframe —— **零额外后端开发**。

### 3.2 关键约束：iframe 的主机名必须与 DSH GUI 一致（必读）
SAG cookie 是 `SameSite=Lax`，且若 DSH 与 SAG 被看成**跨站**（hostname 不同，如 DSH 用 `127.0.0.1:3080` 而 iframe 固定写 `localhost:3000`），Lax cookie 不会随 iframe 请求发出，SAG 会一直显示未登录。
> 解法：插件从 `window.location.hostname` **动态推导** iframe 基址（`http://<同主机名>:3000`），并让 REST/MCP 基址同样可配置（默认也取同主机名）。DSH 当前就运行在 `127.0.0.1:3080` → iframe 用 `http://127.0.0.1:3000`，同站，cookie 正常。
> 首次使用：iframe 内会出现 `/login`，输入名字登录一次（7 天 cookie），后续全插件端登录态可用。

### 3.3 需求 5（DSH 会话替代 SAG 问答）—— 可行，主路径 = MCP 挂载
**推荐主路径**：
1. 插件 host 端启动时 `POST /auth/login` 换取 JWT（持久化到插件 storage，失效时按 REST 重新登录）。
2. 用 `ctx.mcp`/loader 装配 `dsh-mcp-client` 条目：`{transport:"streamable-http", url:"http://127.0.0.1:8000/mcp/", headers:{Authorization:"Bearer <JWT>"}, serverName:"sag"}`。
3. 工具自动出现：`mcp__sag__search`、`mcp__sag__list_sources`、`mcp__sag__get_entity`……
4. 随包技能 `skills/sag-knowledge/SKILL.md`：
   - `/sag <问题>` 或 `/sag <问题> @库1 @库2` → 先 `list_sources` 解析 @（映射 source_id），再按库 `search(query, source_id)` 或多库全局 `POST /search`(source_ids[]) 取证据，DSH 模型用证据生成带引用答案。
   - 模型判断"回答依赖知识库事实时"自动调用工具，无需 / 也可（技能内说明）。
5. 效果：**SAG 引擎负责检索（保留其 SOTA 检索质量），DSH 当前会话模型负责提问理解与答案生成** —— 即用户要的"在他的基础上做了一个升级"。

**备选路径（不依赖 MCP）**：插件 host 端直接注册自定义工具（如 `sag_search` / `sag_list_sources`），内部调用 SAG REST `/search`（含 `source_ids`），返回证据文本 + 引用。更可控（可加鉴权缓存、超时、多库合并），但要多写一层工具胶水；MCP 路径几乎零胶水。

**澄清一点**："用当前会话的模型去完成检索"的准确实现是——模型**编排**检索（决定查什么、选哪些库、用哪条策略），**检索计算仍由 SAG 引擎完成**（嵌入、事件-实体、动态超边是 SAG 索引的资产，跳过它就丢掉了 SAG 的价值）；模型负责**生成**答案。这是对检索/生成职责的正确拆分，也是"升级"的落点。

### 3.4 风险与注意点
| 风险 | 说明 | 对策 |
| --- | --- | --- |
| JWT 过期 | SAG token 默认有效期有限 | 工具调用前校验/触 401 时自动重登（host 端做，模型无感） |
| SAG 端口可配置 | 3000/8000 若改绑 | 插件设置项暴露 baseUrl（默认推导） |
| 窄 iframe 体验 | SAG 有自己的左侧导航，窄面板内会密集 | 插件让 iframe 占满 overlay；未来可加 `?embed` 精简模式（不改 SAG 也可接受） |
| MCP 挂载影响面 | 工具名 `mcp__sag__*` 全局可见 | 挂在 session 级 agent.ctx 或插件启动项控制开关 |
| 会话内 @ 解析 | 需在消息预处理钩子里查 SAG 库列表 | 复用 dsh-at-file 钩子模式，host 端缓存库列表，`/sag` 时刷新 |

---

## 4. 实现方案（插件结构）

**包名**：`@dsh-external/dsh-sag-knowledge`（或自选 scope），dsh-super-injector 生成骨架后填充。

```
dsh-sag-knowledge/
├── package.json            # dsh.bundle.patch → cordis.patch.yml；dsh.client.inject
├── cordis.patch.yml        # host 插件装配（mcp-client 条目 + sag 插件 body）
├── src/
│   ├── index.ts            # host 半：ctx.sag 服务（登录/重登/JWT 缓存、sources 列表缓存）
│   ├── tools.ts            # (可选) REST 直连工具 sag_search / sag_list_sources
│   ├── typert.ts           # (可选) remote 命名空间，让 client 拿到 sag 状态
│   └── client/
│       ├── index.ts        # client 半：slots.inject 注册
│       ├── SidebarNav.tsx  # sidebar.footer.action 项（order:-1，任务板上方），图标+“SAG 知识库”
│       ├── SagOverlay.tsx  # shell.overlay 全屏面板：顶部 tabs [知识库搜索|知识库管理] + iframe
│       ├── SagSettingsSection.tsx  # settings.section：tabs [知识库|模型|集成|图谱|诊断] + iframe
│       ├── AtKbMention.tsx # 消息预处理钩子：@库名 → source_id 引用注入（可选增强）
│       └── styles.ts
└── skills/
    └── sag-knowledge/
        └── SKILL.md        # /sag、@选库、search/list_sources 使用指南
```

**装配/生命周期**
1. `dev_scaffold_plugin`(hybrid) 生成骨架 → 填充上述文件 → `dev_build_plugin` → `dev_inject_plugin`（免重启热注入）。
2. host 启动：`ctx.sag` 服务登录 SAG 拿 JWT → 装配 `mcp-client` 条目（streamable-http + Authorization header）→ 工具 `mcp__sag__*` 自动进会话。
3. client 端：三个注册点（sidebar.footer.action / shell.overlay / settings.section）。
4. 技能挂载后，会话里 `/sag 问题 @库A @库B` 直接可用；模型也会在需要时自主调用 `mcp__sag__search`。

**验收清单**
- [ ] 侧边栏左下出现「SAG 知识库」（任务板按钮上方），点击开全屏面板
- [ ] 面板首页顶部 tabs：知识库搜索（/search iframe）/ 知识库管理（/knowledge iframe）
- [ ] DSH 设置页出现「SAG 知识库插件设置」，5 个 tab 分别 iframe /settings?tab=knowledge|model|integrations|graph|diagnostics
- [ ] iframe 内首次登录一次，随后保持登录态（同站 cookie）
- [ ] 会话内 `/sag <问> @库` 检索并带引用回答；无需 / 时模型也能自主检索
- [ ] 卸载插件（dev_uninject_plugin）后无残留

**依赖清单（peerDeps，参考 taskboard/setting-mcp 的声明版本）**：
`@deepseek-ai/dsh-client-runtime`、`dsh-client-ui-slots`、`dsh-client-ui-sidebar`、`dsh-client-ui-settings`、`dsh-client-locale`、`dsh-api-remotes`、`dsh-tools`、`dsh-host-apiproxy`（如需 REST）、`dsh-host-webserver`。

---

## 5. 一句话结论

SAG 具备完整可嵌入面（可 iframe 的 web 页面 + 干净 REST + 原生 MCP），DSH 具备完整插件注入面（sidebar/overlay/settings 槽位 + MCP 客户端自动工具化 + 命令/技能/@提及），**需求 1–4 是小成本的 iframe 封装插件，需求 5 是通过 MCP 挂载 + 技能把"检索归 SAG、生成归 DSH 会话模型"的升级**——全部可在不修改 SAG 代码、不新建服务的前提下实现。