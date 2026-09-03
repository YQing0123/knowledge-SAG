"use client";

import * as React from "react";
import {
  Archive,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CirclePlus,
  ClipboardPenLine,
  Flame,
  FolderKanban,
  GanttChartSquare,
  Grid2X2,
  LayoutList,
  Lightbulb,
  ListTodo,
  Plus,
  Search,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { getToken } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";
import type { Project, ProjectSnapshot, ProjectStage, ProjectTask, ProjectStatus } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useApp } from "@/components/features/app-shell";

type ViewMode = "gantt" | "list" | "calendar" | "kanban";
type WorkspaceTab = "overview" | "projects";
type TaskFilter = "all" | ProjectStatus;
type GanttScale = "day" | "week" | "month" | "quarter";

type ProjectForm = {
  name: string;
  description: string;
  color: string;
  project_type: "stage" | "nostage";
  stage_index: number;
  start_date: string | null;
  end_date: string | null;
};

type TaskForm = {
  project_id: string;
  parent_id: string | null;
  title: string;
  status: ProjectStatus;
  priority: string | null;
  start_date: string | null;
  due_date: string | null;
  notes: string;
};

type TaskDialogState = {
  task?: ProjectTask;
  parentTask?: ProjectTask;
};

type TreeTask = { task: ProjectTask; depth: number; hasChildren: boolean };

const snapshotCaches = new Map<string, ProjectSnapshot>();
const projectColors = ["#3b82f6", "#0ea5e9", "#14b8a6", "#22c55e", "#eab308", "#f97316", "#ef4444", "#a855f7"];
const statusLabels: Record<ProjectStatus, string> = {
  todo: "待办",
  "in-progress": "进行中",
  blocked: "已阻塞",
  done: "已完成",
  cancelled: "已取消",
};
const priorityLabels: Record<string, string> = {
  "重要且紧急": "高",
  "重要不紧急": "中",
  "紧急不重要": "中",
  "不重要不紧急": "低",
};

function isoToday() {
  const date = new Date();
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 10);
}
function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function formatLocalDate(date: Date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function dateAt(value: string) {
  return new Date(`${value}T00:00:00`);
}

function localDatePart(value: string | null) {
  return value ? value.slice(0, 10) : null;
}

function isOpen(task: ProjectTask) {
  return task.status !== "done" && task.status !== "cancelled";
}

function isToday(task: ProjectTask, today = isoToday()) {
  return isOpen(task) && (
    task.start_date === today || task.due_date === today || Boolean(task.start_date && task.due_date && task.start_date < today && task.due_date > today)
  );
}

function isOverdue(task: ProjectTask, today = isoToday()) {
  return isOpen(task) && Boolean(task.due_date && task.due_date < today);
}

function daysOverdue(task: ProjectTask, today = isoToday()) {
  if (!task.due_date) return 0;
  return Math.max(1, Math.floor((dateAt(today).getTime() - dateAt(task.due_date).getTime()) / 86_400_000));
}

function dateRange(task: ProjectTask) {
  return [task.start_date, task.due_date].filter(Boolean).map((value) => value?.slice(5)).join(" -> ") || "未设置日期";
}

function priorityClass(task: ProjectTask) {
  return task.priority === "重要且紧急" ? "po-p-high" : task.priority ? "po-p-med" : "po-p-low";
}

function statusClass(status: ProjectStatus) {
  return status === "done" ? "po-done" : status === "in-progress" ? "po-progress" : status === "blocked" ? "po-blocked" : status === "cancelled" ? "po-cancelled" : "po-todo";
}

function weekBounds(today = isoToday()) {
  const weekday = dateAt(today).getDay();
  const offset = weekday === 0 ? -6 : 1 - weekday;
  return { start: addDays(today, offset), end: addDays(today, offset + 6) };
}

function taskSort(a: ProjectTask, b: ProjectTask) {
  const aPriority = a.priority === "重要且紧急" ? 0 : a.priority ? 1 : 2;
  const bPriority = b.priority === "重要且紧急" ? 0 : b.priority ? 1 : 2;
  return aPriority - bPriority || (a.start_date ?? "9999-12-31").localeCompare(b.start_date ?? "9999-12-31") || a.sort_order - b.sort_order;
}

function projectProgress(project: Project, tasks: ProjectTask[]) {
  const current = tasks.filter((task) => task.project_id === project.id);
  const done = current.filter((task) => task.status === "done").length;
  return { total: current.length, done, percent: current.length ? Math.round((done / current.length) * 100) : 0 };
}

function treeTasks(tasks: ProjectTask[]): TreeTask[] {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const children = new Map<string, ProjectTask[]>();
  const roots: ProjectTask[] = [];
  for (const task of tasks) {
    if (task.parent_id && byId.has(task.parent_id)) {
      children.set(task.parent_id, [...(children.get(task.parent_id) ?? []), task]);
    } else {
      roots.push(task);
    }
  }
  const output: TreeTask[] = [];
  const walk = (task: ProjectTask, depth: number, ancestors: Set<string>) => {
    if (ancestors.has(task.id)) return;
    const nextAncestors = new Set(ancestors);
    nextAncestors.add(task.id);
    const taskChildren = [...(children.get(task.id) ?? [])].sort(taskSort);
    output.push({ task, depth, hasChildren: taskChildren.length > 0 });
    taskChildren.forEach((child) => walk(child, depth + 1, nextAncestors));
  };
  roots.sort(taskSort).forEach((task) => walk(task, 0, new Set()));
  return output;
}

function activityCount(task: ProjectTask, day: string) {
  const start = task.start_date ?? task.due_date ?? localDatePart(task.created_at);
  const end = task.due_date ?? task.start_date ?? start;
  return start && end && day >= start && day <= end ? 1 : 0;
}

export function ProjectManagementWorkspace({ active = true }: { active?: boolean }) {
  const { appMode } = useApp();
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [tasks, setTasks] = React.useState<ProjectTask[]>([]);
  const [stages, setStages] = React.useState<ProjectStage[]>([]);
  const [tab, setTab] = React.useState<WorkspaceTab>("overview");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [view, setView] = React.useState<ViewMode>("gantt");
  const [filter, setFilter] = React.useState<TaskFilter>("all");
  const [query, setQuery] = React.useState("");
  const [ganttScale, setGanttScale] = React.useState<GanttScale>("week");
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [captureSaving, setCaptureSaving] = React.useState(false);
  const [taskDialog, setTaskDialog] = React.useState<TaskDialogState | null>(null);
  const [projectDialog, setProjectDialog] = React.useState<Project | null | undefined>(undefined);
  const [capture, setCapture] = React.useState("");
  const [calendarMonth, setCalendarMonth] = React.useState(() => new Date(`${isoToday()}T00:00:00`));
  const [isDark, setIsDark] = React.useState(false);

  const load = React.useCallback(async () => {
    const token = getToken() ?? "";
    const cached = token ? snapshotCaches.get(token) : undefined;
    if (cached) {
      setProjects(cached.projects);
      setTasks(cached.tasks);
      setStages(cached.stages);
      setLoading(false);
    } else {
      setLoading(true);
    }
    try {
      const snapshot = await api.projectSnapshot();
      if (token) snapshotCaches.set(token, snapshot);
      setProjects(snapshot.projects);
      setTasks(snapshot.tasks);
      setStages(snapshot.stages);
      setSelectedId((current) => current && snapshot.projects.some((project) => project.id === current) ? current : null);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "项目数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (active && appMode === "normal") void load();
  }, [active, appMode, load]);

  React.useEffect(() => {
    const html = document.documentElement;
    const sync = () => {
      const dark = html.classList.contains("dark");
      setIsDark(dark);
      document.body.classList.toggle("theme-dark", dark);
      document.body.classList.toggle("theme-light", !dark);
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(html, { attributes: true, attributeFilter: ["class"] });
    return () => {
      observer.disconnect();
      document.body.classList.remove("theme-dark", "theme-light");
    };
  }, []);

  const selected = projects.find((project) => project.id === selectedId) ?? null;
  const scopedTasks = selected ? tasks.filter((task) => task.project_id === selected.id) : tasks;
  const visibleTasks = React.useMemo(() => {
    const search = query.trim().toLowerCase();
    return scopedTasks.filter((task) => (
      (filter === "all" || task.status === filter) &&
      (!search || `${task.title} ${task.notes} ${task.tags.join(" ")}`.toLowerCase().includes(search))
    ));
  }, [filter, query, scopedTasks]);

  async function refreshAfter(action: () => Promise<unknown>, success?: string) {
    setSaving(true);
    try {
      await action();
      snapshotCaches.delete(getToken() ?? "");
      if (success) toast.success(success);
      await load();
      return true;
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "保存失败");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function setTaskStatus(task: ProjectTask, status: ProjectStatus) {
    await refreshAfter(() => api.updateTask(task.id, { status, version: task.version }));
  }

  async function toggleTask(task: ProjectTask) {
    await setTaskStatus(task, task.status === "done" ? "todo" : "done");
  }

  async function saveProject(data: ProjectForm) {
    const success = await refreshAfter(
      () => projectDialog ? api.updateProject(projectDialog.id, { ...data, version: projectDialog.version }) : api.createProject(data),
      projectDialog ? "项目已更新" : "项目已创建",
    );
    if (success) setProjectDialog(undefined);
  }

  async function saveTask(data: TaskForm) {
    const success = await refreshAfter(
      () => taskDialog?.task
        ? api.updateTask(taskDialog.task.id, { ...data, version: taskDialog.task.version })
        : api.createTask({ ...data, sort_order: tasks.filter((task) => task.project_id === data.project_id).length }),
      taskDialog?.task ? "任务已更新" : "任务已创建",
    );
    if (success) setTaskDialog(null);
  }

  async function archiveProject(project: Project) {
    if (window.confirm(`归档项目“${project.name}”？`)) {
      await refreshAfter(() => api.archiveProject(project.id), "项目已归档");
    }
  }

  async function deleteTask(task: ProjectTask) {
    if (window.confirm(`删除任务“${task.title}”？`)) {
      await refreshAfter(() => api.deleteTask(task.id), "任务已删除");
    }
  }

  async function setStage(project: Project, stageIndex: number) {
    await refreshAfter(() => api.updateProject(project.id, { stage_index: stageIndex, version: project.version }));
  }

  async function saveCapture() {
    const content = capture.trim();
    if (!content || captureSaving) return;
    setCaptureSaving(true);
    try {
      const result = await api.createQuickCapture(content);
      setCapture("");
      toast.success(`已保存为 ${result.filename}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "快速捕捉保存失败");
    } finally {
      setCaptureSaving(false);
    }
  }

  function openTask(task: ProjectTask) {
    setSelectedId(task.project_id);
    setTab("projects");
    setTaskDialog({ task });
  }

  function createChildTask(parentTask: ProjectTask) {
    setSelectedId(parentTask.project_id);
    setTaskDialog({ parentTask });
  }

  return (
    <div className="dashboard-plugin" data-theme={isDark ? "dark" : "light"}>
      <DashboardBanner projects={projects} tasks={tasks} />
      <DashboardNav tab={tab} onTab={setTab} />
      {loading ? <div className="po-empty">加载工作台数据...</div> : tab === "overview" ? (
        <OverviewBoard
          projects={projects}
          tasks={tasks}
          stages={stages}
          capture={capture}
          captureSaving={captureSaving}
          onCaptureChange={setCapture}
          onCapture={saveCapture}
          onTask={openTask}
          onToggle={(task) => void toggleTask(task)}
          onStage={(project, index) => void setStage(project, index)}
        />
      ) : (
        <ProjectBoard
          projects={projects}
          tasks={visibleTasks}
          allTasks={tasks}
          stages={stages}
          selected={selected}
          selectedId={selectedId}
          view={view}
          filter={filter}
          query={query}
          ganttScale={ganttScale}
          month={calendarMonth}
          onSelect={setSelectedId}
          onView={setView}
          onFilter={setFilter}
          onQuery={setQuery}
          onGanttScale={setGanttScale}
          onNewProject={() => setProjectDialog(null)}
          onNewTask={() => setTaskDialog({})}
          onChildTask={createChildTask}
          onTask={openTask}
          onToggle={(task) => void toggleTask(task)}
          onStatusChange={(task, status) => void setTaskStatus(task, status)}
          onDelete={(task) => void deleteTask(task)}
          onStage={(project, index) => void setStage(project, index)}
          onArchive={(project) => void archiveProject(project)}
          onMonth={setCalendarMonth}
        />
      )}
      <TaskDialog
        open={taskDialog !== null}
        task={taskDialog?.task}
        parentTask={taskDialog?.parentTask}
        projects={projects}
        allTasks={tasks}
        saving={saving}
        onOpenChange={(open) => !open && setTaskDialog(null)}
        onSave={saveTask}
      />
      <ProjectDialog
        open={projectDialog !== undefined}
        project={projectDialog ?? undefined}
        stages={stages}
        saving={saving}
        onOpenChange={(open) => !open && setProjectDialog(undefined)}
        onSave={saveProject}
      />
    </div>
  );
}

function DashboardBanner({ projects, tasks }: { projects: Project[]; tasks: ProjectTask[] }) {
  const open = tasks.filter((task) => isOpen(task));
  const done = tasks.filter((task) => task.status === "done");
  const overdue = tasks.filter((task) => isOverdue(task));
  const today = isoToday();
  const days = Array.from({ length: 84 }, (_, index) => addDays(today, index - 83));
  const values = days.map((day) => tasks.reduce((count, task) => count + activityCount(task, day), 0));
  const maximum = Math.max(...values, 1);
  const heatLevel = (value: number) => value === 0 ? 0 : Math.min(4, Math.ceil((value / maximum) * 4));
  const completedPercent = tasks.length ? Math.round((done.length / tasks.length) * 100) : 0;
  const overduePercent = open.length ? Math.round((overdue.length / open.length) * 100) : 0;
  const now = new Date();

  return (
    <section className="ad-banner ad-banner--stats" aria-label="工作台摘要">
      <div className="ad-banner-stats">
        <div className="ad-banner-stat-col ad-banner-stat-col--left">
          <div className="ad-banner-stat-hero">
            <ClipboardPenLine size={22} />
            <span className="ad-banner-stat-num">{tasks.length}</span>
            <span className="ad-banner-stat-label ad-banner-stat-label--inline">总任务</span>
          </div>
          <div className="ad-banner-stat-strip">
            <span>当前待办 {open.length}</span>
            <span>已完成 {done.length}</span>
            <span>项目 {projects.length}</span>
          </div>
        </div>
        <div className="ad-banner-stat-col ad-banner-stat-col--center">
          <div className="ad-banner-stat-hero">
            <span className="ad-banner-stat-title-prefix">鸣谦 · 工作台</span>
            <Flame className="ad-banner-stat-icon" />
            <span className="ad-banner-stat-num">{Math.max(1, done.length)}天</span>
            <span className="ad-banner-stat-label ad-banner-stat-label--inline">连续记录</span>
          </div>
          <div className="ad-banner-stat-sub">任务排期热力 · 最近 12 周</div>
          <div className="ad-banner-stat-chart">
            <div className="ad-banner-heatmap" aria-label="按任务日期统计的热力图">
              {days.map((day, index) => {
                const value = values[index];
                const level = heatLevel(value);
                return <span key={day} title={`${day}：${value} 项任务`} className={`ad-banner-heatmap-cell${level ? ` ad-banner-heatmap-cell--l${level}` : ""}${day === today ? " ad-banner-heatmap-cell--today" : ""}`} />;
              })}
            </div>
          </div>
        </div>
        <div className="ad-banner-stat-col ad-banner-stat-col--right">
          <BannerMetric label="任务完成率" value={completedPercent} />
          <BannerMetric label="任务逾期率" value={overduePercent} />
        </div>
      </div>
      <div className="ad-banner-meta">
        <span className="ad-header__date">{now.toLocaleDateString("zh-CN")} {now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
        <span className="ad-header__meta">星期{["日", "一", "二", "三", "四", "五", "六"][now.getDay()]} · 工作进行中</span>
      </div>
    </section>
  );
}

function BannerMetric({ label, value }: { label: string; value: number }) {
  return <div className="ad-banner-stat-prog">
    <div className="ad-banner-stat-prog-head"><span className="ad-banner-stat-prog-title">{label}</span><span className="ad-banner-stat-prog-val">{value}%</span></div>
    <span className="ad-banner-stat-prog-track"><span className="ad-banner-stat-prog-fill" style={{ width: `${value}%` }} /></span>
  </div>;
}

function DashboardNav({ tab, onTab }: { tab: WorkspaceTab; onTab: (tab: WorkspaceTab) => void }) {
  return (
    <nav className="ad-nav sag-workbench-nav" aria-label="工作台导航">
      <button type="button" className={tab === "overview" ? "is-active" : ""} onClick={() => onTab("overview")}><FolderKanban size={16} />主页</button>
      <button type="button" className={tab === "projects" ? "is-active" : ""} onClick={() => onTab("projects")}><Grid2X2 size={16} />项目管理</button>
    </nav>
  );
}

function OverviewBoard({ projects, tasks, stages, capture, captureSaving, onCaptureChange, onCapture, onTask, onToggle, onStage }: {
  projects: Project[];
  tasks: ProjectTask[];
  stages: ProjectStage[];
  capture: string;
  captureSaving: boolean;
  onCaptureChange: (value: string) => void;
  onCapture: () => void;
  onTask: (task: ProjectTask) => void;
  onToggle: (task: ProjectTask) => void;
  onStage: (project: Project, index: number) => void;
}) {
  const today = isoToday();
  const week = weekBounds(today);
  const todo = [...tasks].filter((task) => isOpen(task)).sort(taskSort);
  const todayTasks = tasks.filter((task) => isToday(task, today));
  const doneToday = tasks.filter((task) => task.status === "done" && localDatePart(task.complete_time) === today).length;
  const done = tasks.filter((task) => task.status === "done");
  const weekDone = done.filter((task) => {
    const value = localDatePart(task.complete_time);
    return value && value >= week.start && value <= week.end;
  }).sort((a, b) => (b.complete_time ?? "").localeCompare(a.complete_time ?? ""));
  const overdue = todo.filter((task) => isOverdue(task, today));

  return (
    <main className="ad-board sag-overview-board" style={{ "--ad-cols": 4, "--ad-row-h": "244px" } as React.CSSProperties}>
      <section className="ad-card" style={{ "--cols": 1, "--rows": 1 } as React.CSSProperties}>
        <CardHead icon={Lightbulb} title="快速捕捉" />
        <div className="ad-qc">
          <textarea className="ad-qc__area" value={capture} onChange={(event) => onCaptureChange(event.target.value)} placeholder="记录一闪而过的想法..." />
          <div className="ad-qc__row"><button className="ad-qc__cta" type="button" disabled={!capture.trim() || captureSaving} onClick={onCapture}>{captureSaving ? "保存中" : "捕捉"}</button></div>
        </div>
      </section>
      <TaskCard title="TODO" icon={ListTodo} hint={`${todo.length} 项待办`} tasks={todo} onToggle={onToggle} empty="暂无待办" />
      <TaskCard title="本周已完成" icon={CheckCircle2} hint={`${weekDone.length} 项`} tasks={weekDone} completed onToggle={onToggle} empty="本周尚未完成任务" />
      <OverdueCard tasks={overdue} onTask={onTask} />
      <section className="ad-card sag-project-card" style={{ "--cols": 2, "--rows": 1 } as React.CSSProperties}>
        <div className="ad-card__head ad-card__head--proj"><h3 className="ad-card__title"><FolderKanban size={15} />项目情况</h3><span className="ad-card__hint--inline">{projects.length} 个项目</span></div>
        <div className="ad-proj"><div className="ad-proj__list">{projects.map((project) => <ProjectOverviewRow key={project.id} project={project} tasks={tasks} stages={stages} onStage={onStage} />)}{!projects.length && <Empty text="创建一个项目开始安排工作" />}</div></div>
      </section>
      <section className="ad-card sag-progress-card" style={{ "--cols": 2, "--rows": 1 } as React.CSSProperties}>
        <CardHead icon={CheckCircle2} title="工作进度" hint="实时计算" />
        <div className="ad-dp sag-progress-pair">
          <ProgressRing percent={todayTasks.length ? Math.round((doneToday / todayTasks.length) * 100) : 0} label={`今日已完成 ${doneToday} / 今日总任务 ${todayTasks.length}`} />
          <ProgressRing percent={tasks.length ? Math.round((done.length / tasks.length) * 100) : 0} label={`总完成 ${done.length} / 总任务 ${tasks.length}`} />
        </div>
      </section>
    </main>
  );
}

function CardHead({ icon: Icon, title, hint }: { icon: React.ComponentType<{ size?: number }>; title: string; hint?: string }) {
  return <div className="ad-card__head"><h3 className="ad-card__title"><Icon size={15} />{title}</h3>{hint && <span className="ad-card__hint">{hint}</span>}</div>;
}

function TaskCard({ title, icon, hint, tasks, completed = false, onToggle, empty }: {
  title: string;
  icon: React.ComponentType<{ size?: number }>;
  hint: string;
  tasks: ProjectTask[];
  completed?: boolean;
  onToggle: (task: ProjectTask) => void;
  empty: string;
}) {
  return <section className="ad-card" style={{ "--cols": 1, "--rows": 1 } as React.CSSProperties}>
    <CardHead icon={icon} title={title} hint={hint} />
    <div className="ad-todo">
      {tasks.slice(0, 7).map((task) => <button key={task.id} type="button" className={`ad-todo__item ${completed ? "is-done" : ""}`} onClick={() => onToggle(task)}>
        <span className="ad-todo__check" />
        <span className="ad-todo__text">{task.title}</span>
        {task.priority && !completed && <span className="ad-todo__tag" data-prio={task.priority}>{priorityLabels[task.priority] ?? task.priority}</span>}
      </button>)}
      {!tasks.length && <Empty text={empty} />}
    </div>
  </section>;
}

function OverdueCard({ tasks, onTask }: { tasks: ProjectTask[]; onTask: (task: ProjectTask) => void }) {
  return <section className="ad-card" style={{ "--cols": 1, "--rows": 1 } as React.CSSProperties}>
    <CardHead icon={TriangleAlert} title="逾期提醒" hint={tasks.length ? `${tasks.length} 项` : "正常"} />
    <div className="ad-wo sag-overdue-list">
      {tasks.slice(0, 5).map((task) => <button type="button" key={task.id} className="ad-wo__list-item" onClick={() => onTask(task)}>
        <span className="ad-wo__text">{task.title}</span><span className="sag-overdue-tag">逾期 {daysOverdue(task)} 天</span>
      </button>)}
      {!tasks.length && <Empty text="没有逾期事项" />}
    </div>
  </section>;
}

function Empty({ text }: { text: string }) {
  return <p className="po-empty">{text}</p>;
}

function ProgressRing({ percent, label }: { percent: number; label: string }) {
  const radius = 35;
  const total = 2 * Math.PI * radius;
  return <div className="sag-progress-ring-wrap"><div className="ad-dp__ring"><svg viewBox="0 0 80 80"><circle className="ad-track" cx="40" cy="40" r={radius} fill="none" strokeWidth="8" /><circle className="ad-fill" cx="40" cy="40" r={radius} fill="none" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${total * percent / 100} ${total}`} /></svg><div className="ad-dp__center"><strong className="ad-dp__pct">{percent}%</strong></div></div><p className="ad-dp__stat">{label}</p></div>;
}

function ProjectOverviewRow({ project, tasks, stages, onStage }: { project: Project; tasks: ProjectTask[]; stages: ProjectStage[]; onStage: (project: Project, index: number) => void }) {
  const progress = projectProgress(project, tasks);
  return <div className="ad-proj__row">
    <span className="ad-proj__dot" style={{ background: project.color }} />
    <div className="ad-proj__info"><strong className="ad-proj__name">{project.name}</strong><span className="ad-proj__meta">{progress.total} 任务 · {progress.done} 已完成 · {progress.percent}%</span></div>
    {project.project_type === "nostage" ? <span className="ad-proj__meta sag-long-term">长期项目</span> : <div className="ad-proj__track"><div className="ad-proj__stages">{stages.map((stage, index) => <button type="button" key={stage.id} title={`切换到${stage.label}`} className={`ad-proj__stage ${index < project.stage_index ? "is-done" : ""} ${index === project.stage_index ? "is-current" : ""}`} onClick={() => onStage(project, index)}><span className="ad-pip" /><span>{stage.label}</span></button>)}</div></div>}
  </div>;
}

function ProjectBoard({ projects, tasks, allTasks, stages, selected, selectedId, view, filter, query, ganttScale, month, onSelect, onView, onFilter, onQuery, onGanttScale, onNewProject, onNewTask, onChildTask, onTask, onToggle, onStatusChange, onDelete, onStage, onArchive, onMonth }: {
  projects: Project[];
  tasks: ProjectTask[];
  allTasks: ProjectTask[];
  stages: ProjectStage[];
  selected: Project | null;
  selectedId: string | null;
  view: ViewMode;
  filter: TaskFilter;
  query: string;
  ganttScale: GanttScale;
  month: Date;
  onSelect: (id: string | null) => void;
  onView: (view: ViewMode) => void;
  onFilter: (filter: TaskFilter) => void;
  onQuery: (query: string) => void;
  onGanttScale: (scale: GanttScale) => void;
  onNewProject: () => void;
  onNewTask: () => void;
  onChildTask: (task: ProjectTask) => void;
  onTask: (task: ProjectTask) => void;
  onToggle: (task: ProjectTask) => void;
  onStatusChange: (task: ProjectTask, status: ProjectStatus) => void;
  onDelete: (task: ProjectTask) => void;
  onStage: (project: Project, index: number) => void;
  onArchive: (project: Project) => void;
  onMonth: (month: Date) => void;
}) {
  const viewTabs: Array<{ value: ViewMode; icon: React.ComponentType<{ size?: number }>; label: string }> = [
    { value: "gantt", icon: GanttChartSquare, label: "甘特图" },
    { value: "list", icon: LayoutList, label: "列表" },
    { value: "calendar", icon: CalendarDays, label: "日历" },
    { value: "kanban", icon: Grid2X2, label: "看板" },
  ];

  return <section className="po-board"><div className="po-container">
    <aside className="po-sidebar">
      <button className="po-add-btn sag-sidebar-create" type="button" onClick={onNewProject}><CirclePlus size={15} />新建项目</button>
      <div className="po-sidebar__list">
        <button type="button" className={`po-sidebar__item ${selectedId === null ? "is-active" : ""}`} onClick={() => onSelect(null)}><span className="po-dot" style={{ color: "#7ba7ff", background: "#7ba7ff" }} />全部项目 <span className="po-count">{allTasks.filter((task) => isOpen(task)).length}/{allTasks.length}</span></button>
        {projects.map((project) => <button type="button" key={project.id} className={`po-sidebar__item ${selectedId === project.id ? "is-active" : ""}`} onClick={() => onSelect(project.id)}><span className="po-dot" style={{ color: project.color, background: project.color }} />{project.name}<span className="po-count">{project.active_count}/{project.task_count}</span></button>)}
      </div>
    </aside>
    <div className="po-main">
      <div className="po-tabs">
        {viewTabs.map(({ value, icon: Icon, label }) => <button type="button" key={value} className={`po-tab ${view === value ? "is-active" : ""}`} onClick={() => onView(value)}><Icon size={15} />{label}</button>)}
        <span className="sag-toolbar-spacer" />
        {selected && <button type="button" className="po-add-btn sag-archive-btn" onClick={() => onArchive(selected)}><Archive size={14} />归档项目</button>}
        <button type="button" className="po-add-btn op-new-btn" onClick={onNewTask}><Plus size={14} />新建任务</button>
      </div>
      {selected && <StageBar project={selected} stages={stages} onStage={onStage} />}
      <div className="po-toolbar">
        <span className="po-toolbar__label">状态筛选</span>
        {(["all", "todo", "in-progress", "blocked", "done", "cancelled"] as TaskFilter[]).map((item) => <button type="button" key={item} className={`po-chip ${filter === item ? "is-active" : ""}`} onClick={() => onFilter(item)}>{item === "all" ? "全部" : statusLabels[item]}</button>)}
        <label className="po-search"><Search size={13} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="搜索任务" /></label>
      </div>
      <div className="po-content">
        {view === "gantt" && <Gantt tasks={tasks} projects={projects} scale={ganttScale} onScale={onGanttScale} onTask={onTask} onChildTask={onChildTask} onToggle={onToggle} onStatusChange={onStatusChange} onDelete={onDelete} />}
        {view === "list" && <TaskTable tasks={tasks} projects={projects} onTask={onTask} onToggle={onToggle} onStatusChange={onStatusChange} onDelete={onDelete} />}
        {view === "calendar" && <Calendar tasks={tasks} month={month} onMonth={onMonth} onTask={onTask} />}
        {view === "kanban" && <Kanban tasks={tasks} projects={projects} onTask={onTask} onStatusChange={onStatusChange} />}
      </div>
    </div>
  </div></section>;
}

function StageBar({ project, stages, onStage }: { project: Project; stages: ProjectStage[]; onStage: (project: Project, index: number) => void }) {
  if (project.project_type === "nostage") return <div className="po-stage-bar"><span className="po-stage-label">长期项目</span></div>;
  return <div className="po-stage-bar">{stages.map((stage, index) => <React.Fragment key={stage.id}><button type="button" className={`po-stage-node ${index < project.stage_index ? "is-done" : ""} ${index === project.stage_index ? "is-current" : ""}`} onClick={() => onStage(project, index)}><span className="po-stage-dot" /><span className="po-stage-label">{stage.label}</span></button>{index < stages.length - 1 && <span className={`po-stage-conn ${index < project.stage_index ? "is-done" : ""}`} />}</React.Fragment>)}</div>;
}

function ganttConfig(scale: GanttScale) {
  const today = isoToday();
  if (scale === "day") return { start: addDays(today, -14), count: 42, unit: 36, label: (index: number) => addDays(addDays(today, -14), index).slice(5), index: (value: string) => Math.floor((dateAt(value).getTime() - dateAt(addDays(today, -14)).getTime()) / 86_400_000) };
  if (scale === "week") {
    const bounds = weekBounds(today);
    const start = addDays(bounds.start, -28);
    return { start, count: 16, unit: 74, label: (index: number) => `第${index + 1}周`, index: (value: string) => Math.floor((dateAt(value).getTime() - dateAt(start).getTime()) / 604_800_000) };
  }
  const now = dateAt(today);
  const startMonth = scale === "month" ? new Date(now.getFullYear(), now.getMonth() - 5, 1) : new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 - 9, 1);
  const count = scale === "month" ? 12 : 8;
  const multiplier = scale === "month" ? 1 : 3;
  const start = formatLocalDate(startMonth);
  return {
    start,
    count,
    unit: scale === "month" ? 96 : 130,
    label: (index: number) => {
      const date = new Date(startMonth.getFullYear(), startMonth.getMonth() + index * multiplier, 1);
      return scale === "month" ? `${date.getMonth() + 1}月` : `${date.getFullYear()} Q${Math.floor(date.getMonth() / 3) + 1}`;
    },
    index: (value: string) => {
      const date = dateAt(value);
      const months = (date.getFullYear() - startMonth.getFullYear()) * 12 + date.getMonth() - startMonth.getMonth();
      return Math.floor(months / multiplier);
    },
  };
}

function Gantt({ tasks, projects, scale, onScale, onTask, onChildTask, onToggle, onStatusChange, onDelete }: {
  tasks: ProjectTask[];
  projects: Project[];
  scale: GanttScale;
  onScale: (scale: GanttScale) => void;
  onTask: (task: ProjectTask) => void;
  onChildTask: (task: ProjectTask) => void;
  onToggle: (task: ProjectTask) => void;
  onStatusChange: (task: ProjectTask, status: ProjectStatus) => void;
  onDelete: (task: ProjectTask) => void;
}) {
  const config = ganttConfig(scale);
  const tree = treeTasks(tasks);
  const projectById = new Map(projects.map((project) => [project.id, project]));
  const todayIndex = config.index(isoToday());
  const style = { "--sag-gantt-unit": `${config.unit}px`, "--sag-gantt-columns": config.count } as React.CSSProperties;
  return <div className="po-gantt sag-gantt">
    <div className="po-gantt__zoom"><span className="po-toolbar__label">范围</span>{(["day", "week", "month", "quarter"] as GanttScale[]).map((item) => <button type="button" key={item} className={`po-gantt__zoom-btn ${scale === item ? "is-active" : ""}`} onClick={() => onScale(item)}>{{ day: "日", week: "周", month: "月", quarter: "季度" }[item]}</button>)}</div>
    <div className="po-gantt__wrap">
      <div className="po-gantt__left sag-gantt-left" style={{ width: 340 }}><div className="po-gantt__left-hd" style={{ height: 58 }}><span className="po-gantt__left-hd-label">任务名称</span></div><div className="po-gantt__left-body">{tree.map(({ task, depth, hasChildren }) => <div key={task.id} className={`po-gantt__label-row ${depth ? "po-gantt__label-row--child" : ""}`} style={{ height: 44, paddingLeft: `${12 + depth * 22}px` }}><button type="button" className="sag-gantt-title" onClick={() => onTask(task)}>{hasChildren && <span className="po-gantt__label-dot">•</span>}<span className="po-gantt__label-title">{task.title}</span></button>{depth === 0 && <button type="button" className="po-gantt__label-add" aria-label={`为${task.title}添加子任务`} title="添加二级任务" onClick={() => onChildTask(task)}><Plus size={14} /></button>}</div>)}{!tree.length && <Empty text="暂无任务" />}</div></div>
      <div className="po-gantt__right"><div className="po-gantt__hdr-sticky"><div className="sag-gantt-grid sag-gantt-header" style={style}>{Array.from({ length: config.count }, (_, index) => <span key={index} className={`po-gantt__hdr-${scale === "quarter" ? "quarter" : scale === "month" ? "month" : scale === "week" ? "week" : "day"}`}>{config.label(index)}</span>)}</div></div><div className="sag-gantt-body" style={style}>{tree.map(({ task, depth }) => {
        const startIndex = Math.max(0, config.index(task.start_date || task.due_date || isoToday()));
        const rawEnd = Math.max(startIndex, config.index(task.due_date || task.start_date || isoToday()));
        const endIndex = Math.min(config.count - 1, rawEnd);
        const hidden = rawEnd < 0 || startIndex >= config.count;
        return <div key={task.id} className="sag-gantt-row">{!hidden && <button type="button" className={`sag-gantt-bar ${task.status === "done" ? "is-done" : ""} ${depth ? "is-child" : ""}`} style={{ gridColumn: `${startIndex + 1} / ${endIndex + 2}`, background: task.status === "blocked" ? "var(--ad-warn)" : task.status === "done" ? "var(--ad-success)" : projectById.get(task.project_id)?.color ?? "var(--ad-accent)" }} title={`${task.title} · ${dateRange(task)}`} onClick={() => onTask(task)} />}</div>;
      })}{todayIndex >= 0 && todayIndex < config.count && <span className="sag-gantt-today" style={{ gridColumn: todayIndex + 1 }} />}</div></div>
    </div>
    <section className="sag-gantt-task-list"><div className="sag-gantt-task-list-head"><LayoutList size={15} />全部任务</div><TaskTable tasks={tasks} projects={projects} onTask={onTask} onToggle={onToggle} onStatusChange={onStatusChange} onDelete={onDelete} compact /></section>
  </div>;
}

function TaskTable({ tasks, projects, onTask, onToggle, onStatusChange, onDelete, compact = false }: { tasks: ProjectTask[]; projects: Project[]; onTask: (task: ProjectTask) => void; onToggle: (task: ProjectTask) => void; onStatusChange: (task: ProjectTask, status: ProjectStatus) => void; onDelete: (task: ProjectTask) => void; compact?: boolean }) {
  const projectById = new Map(projects.map((project) => [project.id, project]));
  const tree = treeTasks(tasks);
  return <div className={`po-tasklist ${compact ? "sag-compact-tasklist" : ""}`}><div className="po-table-wrap"><table className="po-table"><thead><tr><th>任务名称</th><th>优先级</th><th>开始</th><th>截止</th><th>状态</th><th>项目</th><th aria-label="操作" /></tr></thead><tbody>{tree.map(({ task, depth }) => <tr key={task.id}><td><div className="sag-task-name-cell" style={{ paddingLeft: `${depth * 22}px` }}><button type="button" className={`po-check ${task.status === "done" ? "is-done" : ""}`} onClick={() => onToggle(task)} aria-label="切换任务完成状态" /><button type="button" className={`po-name-cell ${depth ? "po-gantt__name-row--child" : ""}`} onClick={() => onTask(task)}>{task.title}</button></div></td><td>{task.priority && <span className={`po-prio ${priorityClass(task)}`}>{priorityLabels[task.priority] ?? task.priority}</span>}</td><td className="po-mono">{task.start_date ?? "-"}</td><td className="po-mono">{task.due_date ?? "-"}</td><td><span className={`po-status ${statusClass(task.status)}`}>{statusLabels[task.status]}</span></td><td><span className="po-mini-dot" style={{ background: projectById.get(task.project_id)?.color }} />{projectById.get(task.project_id)?.name}</td><td><span className="sag-task-actions"><button type="button" title="标记完成" aria-label="标记完成" className="sag-row-action" onClick={() => onStatusChange(task, "done")}><Check size={14} /></button><button type="button" title="取消任务" aria-label="取消任务" className="sag-row-action" onClick={() => onStatusChange(task, "cancelled")}><X size={14} /></button><button type="button" className="po-delete" onClick={() => onDelete(task)} aria-label="删除任务"><Trash2 size={14} /></button></span></td></tr>)}{!tasks.length && <tr><td colSpan={7}><Empty text="暂无匹配任务" /></td></tr>}</tbody></table></div></div>;
}

function Calendar({ tasks, month, onMonth, onTask }: { tasks: ProjectTask[]; month: Date; onMonth: (month: Date) => void; onTask: (task: ProjectTask) => void }) {
  const start = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (start.getDay() + 6) % 7;
  const first = new Date(start);
  first.setDate(1 - offset);
  const days = Array.from({ length: 42 }, (_, index) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + index));
  const dayKey = (date: Date) => formatLocalDate(date);
  return <div className="po-cal"><div className="po-cal__header"><span className="po-cal__title">{month.getFullYear()}年{month.getMonth() + 1}月</span><span className="po-cal__nav"><button type="button" className="po-cal__btn" onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft size={14} /></button><button type="button" className="po-cal__btn" onClick={() => onMonth(new Date(`${isoToday()}T00:00:00`))}>今天</button><button type="button" className="po-cal__btn" onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight size={14} /></button></span></div><div className="po-cal__weekdays">{["一", "二", "三", "四", "五", "六", "日"].map((day) => <span key={day}>{day}</span>)}</div><div className="po-cal__days">{days.map((day) => { const key = dayKey(day); const dayTasks = tasks.filter((task) => task.start_date === key || task.due_date === key || Boolean(task.start_date && task.due_date && task.start_date < key && task.due_date > key)); return <div key={key} className={`po-cal__day ${key === isoToday() ? "is-today" : ""} ${day.getMonth() !== month.getMonth() ? "is-outside" : ""}`}><span className="po-cal__day-num">{day.getDate()}</span>{dayTasks.slice(0, 3).map((task) => <button type="button" key={task.id} className={`po-cal__task ${isOverdue(task) ? "po-cal__task--overdue" : task.status === "done" ? "po-cal__task--done" : "po-cal__task--normal"}`} style={{ "--chip-color": "var(--ad-accent)" } as React.CSSProperties} onClick={() => onTask(task)}>{task.title}</button>)}{dayTasks.length > 3 && <span className="po-cal__day-more">+{dayTasks.length - 3}</span>}</div>; })}</div></div>;
}

function Kanban({ tasks, projects, onTask, onStatusChange }: { tasks: ProjectTask[]; projects: Project[]; onTask: (task: ProjectTask) => void; onStatusChange: (task: ProjectTask, status: ProjectStatus) => void }) {
  const [draggingId, setDraggingId] = React.useState<string | null>(null);
  const [targetStatus, setTargetStatus] = React.useState<ProjectStatus | null>(null);
  const projectById = new Map(projects.map((project) => [project.id, project]));
  const cols: ProjectStatus[] = ["todo", "in-progress", "blocked", "done", "cancelled"];
  const dragging = tasks.find((task) => task.id === draggingId) ?? null;
  return <div className="po-kanban sag-kanban">{cols.map((status) => <div key={status} className={`po-kanban__col ${targetStatus === status ? "po-kanban__col--drag-over" : ""}`} onDragOver={(event) => { event.preventDefault(); setTargetStatus(status); }} onDragLeave={() => setTargetStatus(null)} onDrop={() => { if (dragging && dragging.status !== status) onStatusChange(dragging, status); setDraggingId(null); setTargetStatus(null); }}><div className="po-kanban__hd"><span className={`po-status ${statusClass(status)}`}>{statusLabels[status]}</span><span className="po-kanban__count">{tasks.filter((task) => task.status === status).length}</span></div>{tasks.filter((task) => task.status === status).map((task) => { const project = projectById.get(task.project_id); return <button type="button" draggable key={task.id} className={`po-kanban__card ${draggingId === task.id ? "po-kanban__card--dragging" : ""}`} onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; setDraggingId(task.id); }} onDragEnd={() => { setDraggingId(null); setTargetStatus(null); }} onClick={() => onTask(task)}><strong>{task.title}</strong><span>{dateRange(task)}</span>{project && <span className="sag-project-tag"><i style={{ background: project.color }} />{project.name}</span>}</button>; })}{!tasks.some((task) => task.status === status) && <div className="po-kanban__empty">拖动任务到这里</div>}</div>)}</div>;
}

function TaskDialog({ open, task, parentTask, projects, allTasks, saving, onOpenChange, onSave }: { open: boolean; task?: ProjectTask; parentTask?: ProjectTask; projects: Project[]; allTasks: ProjectTask[]; saving: boolean; onOpenChange: (open: boolean) => void; onSave: (data: TaskForm) => void }) {
  const [form, setForm] = React.useState<TaskForm>({ project_id: "", parent_id: null, title: "", status: "todo", priority: null, start_date: isoToday(), due_date: isoToday(), notes: "" });
  React.useEffect(() => {
    if (!open) return;
    setForm(task ? { project_id: task.project_id, parent_id: task.parent_id, title: task.title, status: task.status, priority: task.priority, start_date: task.start_date, due_date: task.due_date, notes: task.notes } : { project_id: parentTask?.project_id ?? projects[0]?.id ?? "", parent_id: parentTask?.id ?? null, title: "", status: "todo", priority: null, start_date: isoToday(), due_date: isoToday(), notes: "" });
  }, [open, task, parentTask, projects]);
  const patch = (value: Partial<TaskForm>) => setForm((current) => ({ ...current, ...value }));
  const parent = form.parent_id ? allTasks.find((item) => item.id === form.parent_id) : null;
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="sm:max-w-xl"><DialogHeader><DialogTitle>{task ? "任务详情" : parentTask ? "新建二级任务" : "新建任务"}</DialogTitle><DialogDescription>任务状态、时间与项目进度会实时同步。</DialogDescription></DialogHeader><div className="grid gap-4"><label>所属项目<select value={form.project_id} onChange={(event) => patch({ project_id: event.target.value, parent_id: null })}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>{parent && <div className="sag-parent-notice">父任务：{parent.title}</div>}<label>任务名称<Input autoFocus value={form.title} onChange={(event) => patch({ title: event.target.value })} /></label><div className="grid grid-cols-2 gap-3"><label>状态<select value={form.status} onChange={(event) => patch({ status: event.target.value as ProjectStatus })}>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>优先级<select value={form.priority ?? ""} onChange={(event) => patch({ priority: event.target.value || null })}><option value="">未设置</option>{Object.keys(priorityLabels).map((priority) => <option key={priority} value={priority}>{priorityLabels[priority]}</option>)}</select></label></div><div className="grid grid-cols-2 gap-3"><label>开始日期<Input type="date" value={form.start_date ?? ""} onChange={(event) => patch({ start_date: event.target.value || null })} /></label><label>截止日期<Input type="date" value={form.due_date ?? ""} onChange={(event) => patch({ due_date: event.target.value || null })} /></label></div><label>备注<textarea value={form.notes} onChange={(event) => patch({ notes: event.target.value })} rows={3} /></label></div><DialogFooter><Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button><Button disabled={!form.project_id || !form.title.trim() || saving} onClick={() => onSave({ ...form, title: form.title.trim() })}>{saving ? "保存中..." : "保存"}</Button></DialogFooter></DialogContent></Dialog>;
}

function ProjectDialog({ open, project, stages, saving, onOpenChange, onSave }: { open: boolean; project?: Project; stages: ProjectStage[]; saving: boolean; onOpenChange: (open: boolean) => void; onSave: (data: ProjectForm) => void }) {
  const [form, setForm] = React.useState<ProjectForm>({ name: "", description: "", color: projectColors[0], project_type: "stage", stage_index: 0, start_date: isoToday(), end_date: null });
  React.useEffect(() => { if (open) setForm(project ? { name: project.name, description: project.description, color: project.color, project_type: project.project_type, stage_index: project.stage_index, start_date: project.start_date, end_date: project.end_date } : { name: "", description: "", color: projectColors[0], project_type: "stage", stage_index: 0, start_date: isoToday(), end_date: null }); }, [open, project]);
  const patch = (value: Partial<ProjectForm>) => setForm((current) => ({ ...current, ...value }));
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="sm:max-w-xl"><DialogHeader><DialogTitle>{project ? "编辑项目" : "新建项目"}</DialogTitle><DialogDescription>阶段项目支持立项、迭代、完结等自定义节点；长期项目不设阶段。</DialogDescription></DialogHeader><div className="grid gap-4"><label>项目名称<Input autoFocus value={form.name} onChange={(event) => patch({ name: event.target.value })} /></label><label>项目类型<select value={form.project_type} onChange={(event) => patch({ project_type: event.target.value as "stage" | "nostage" })}><option value="stage">阶段项目</option><option value="nostage">长期项目</option></select></label>{form.project_type === "stage" && <label>当前阶段<select value={form.stage_index} onChange={(event) => patch({ stage_index: Number(event.target.value) })}>{stages.map((stage, index) => <option key={stage.id} value={index}>{stage.label}</option>)}</select></label>}<div className="grid grid-cols-2 gap-3"><label>开始日期<Input type="date" value={form.start_date ?? ""} onChange={(event) => patch({ start_date: event.target.value || null })} /></label><label>结束日期<Input type="date" value={form.end_date ?? ""} onChange={(event) => patch({ end_date: event.target.value || null })} /></label></div><div><span>项目颜色</span><div className="mt-2 flex gap-2">{projectColors.map((color) => <button type="button" key={color} className={`size-7 rounded-full border-2 ${form.color === color ? "border-foreground" : "border-transparent"}`} style={{ backgroundColor: color }} onClick={() => patch({ color })} aria-label={`选择 ${color}`} />)}</div></div><label>项目描述<textarea value={form.description} onChange={(event) => patch({ description: event.target.value })} rows={3} /></label></div><DialogFooter><Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button><Button disabled={!form.name.trim() || saving} onClick={() => onSave({ ...form, name: form.name.trim() })}>{saving ? "保存中..." : "保存项目"}</Button></DialogFooter></DialogContent></Dialog>;
}
