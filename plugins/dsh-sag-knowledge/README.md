# @dsh-external/dsh-sag-knowledge

SAG 知识库 × DSH Web 集成插件：**服务仍由 Docker 中的 SAG 提供（`sag-api-1` :8000 / `sag-web-1` :3000），本插件不新建任何后端**。SAG Web 已支持 `?embed=1` 嵌入模式（本插件对 `apps/web` 的少量改动）：

### 嵌入模式（?embed=1）
`apps/web/app/(app)/layout.tsx` 从 `searchParams.embed` 解析（SSR 首帧即生效，无水合闪烁），`AppShell` 据此：
- 隐藏左侧主菜单（`AppSidebar`）与顶部行（`SiteHeader`：侧边栏收起 / 面包屑 / 探索）→ 收起面板图标随之消失；
- `SidebarInset` 取 `w-full`，内容区全宽；
- SAG 内容区内部的次级侧边栏（搜索筛选、知识库树等）保留，可自适应展开收缩。

### 插件三件事
1. **侧边栏入口**（左下角，与任务板**上下排**）：`sidebar.footer.action`，`order:-10`。插件注入全局 CSS `.footerActions{flex-direction:column}`——DSH 该槽位容器默认为横排（flex-row），改竖排后任务板与 SAG 两个全宽按钮上下排列。
2. **首页面板**（`shell.overlay`）：任务板同款**中栏嵌入**几何（复刻 `useFrameInsets`：页面根 `left/right` 取 DSH 外壳 grid 列宽）——左侧 DSH 工作区保留可见，SAG 只占右侧中栏。顶部 tabs **知识库搜索 / 知识库管理**，主体 iframe `?embed=1` 全宽显示 SAG 对应页（`/search`、`/knowledge`）。
3. **设置页分区**（`settings.section`，order:40）：直接 iframe SAG 设置页（`/settings?embed=1`）——SAG 设置页内部自带 8 个设置 tab（账号/Agent/模型/知识库/集成/外观/图谱/诊断），**插件不再包外层 5 tab**。

外加 **会话检索技能**（`skills/sag-knowledge/SKILL.md` + host 工具）：

- `sag_search`：REST 调 `POST /api/v1/search`（支持 `source_ids[]` 多库 @ 限定）。
- `sag_list_sources`：REST 调 `GET /api/v1/sources`（@ 选库解析）。
- 用法：`/sag <问题> [@知识库...]` 快速唤起；模型在回答依赖知识库事实时也会自主调用。
- **职责划分**：SAG 引擎负责检索（事件-实体索引 + 动态超边），DSH 当前会话模型负责提问理解与答案生成（带引用）——对 SAG 内置对话的升级。
- `SKILL.md` 随包分发，宿主 `apply` 启动时自动读取并注册为 `sag-knowledge` 技能（无需拷贝到任何 skills 目录）。

## 同站原则（重要）

SAG Web 用 `SameSite=Lax` cookie 保持登录。iframe 基址与 REST 基址由插件从 DSH 当前页面的 `hostname` 推导：

- DSH GUI 用 `http://127.0.0.1:3080` → iframe `http://127.0.0.1:3000/...`、API `http://127.0.0.1:8000`（**同站，登录态正常**）。
- 首次使用时 iframe 内会出现 SAG 登录页，输入名字登录一次（cookie 7 天），此后插件内全站保持登录。
- host 工具会以 `loginName`（默认 `dsh-sag`）自动注册身份并缓存 JWT，401 自动重登——不依赖 iframe 登录态也能检索。

## 配置（cordis.patch.yml / 注入清单 Config）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `sagApiBase` | `http://127.0.0.1:8000` | SAG REST API 基址 |
| `sagWebBase` | `http://127.0.0.1:3000` | SAG Web 基址（client iframe 默认取同 hostname，此值供覆盖） |
| `loginName` | `dsh-sag` | SAG 本地登录名（自动建身份） |
| `autoLogin` | `true` | 启动时预热 JWT |
| `defaultTopK` | `8` | 检索默认证据条数 |

## 构建与注入

```bash
# host: tsc（DSH_CHECKOUT 指向 deepseek-harness checkout）
DSH_CHECKOUT=<checkout> bash scripts/build.sh
# client: tsdown（用 checkout 的 tsdown，cwd 位于本目录）
<checkout>/node_modules/.bin/tsdown
# 注入（免重启热装配）
dev_inject_plugin <本目录>
# 卸载
dev_uninject_plugin dsh-sag-knowledge
```

## 验收

- [x] 侧边栏左下角「SAG 知识库」与任务板按钮**上下排**（不再横排挤在一起）
- [x] 点击左侧按钮→首页面板：顶部 tabs 知识库搜索/知识库管理，主体 embed iframe；左侧 DSH 工作区保留可见（中栏嵌入）
- [x] DSH 设置页「SAG 知识库」分区：直接嵌入 SAG 设置页（内部自带 8 tab），无外层包装、无顶部行
- [x] 会话内 `/sag <问> [@知识库]` 检索并带引用回答；模型可自主调用 `sag_search`
- [x] `dev_uninject_plugin` 卸载无残留

## SAG 源码改动清单（本轮）

- `apps/web/app/(app)/layout.tsx`：读取 `searchParams.embed` 传给 AppShell（SSR 首帧生效）。
- `apps/web/components/features/app-shell.tsx`：`AppShell` 新增 `embed` prop；embed 时隐藏 `AppSidebar` 与 `SiteHeader`，`SidebarInset` 取全宽。