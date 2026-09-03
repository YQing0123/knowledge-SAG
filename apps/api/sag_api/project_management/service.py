from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.db.base import new_id
from sag_api.project_management.models import Project, ProjectStage, Requirement, Task, TaskDailyNode, WorkReport
from sag_api.project_management.schemas import (
    DailyNodeInput,
    ProjectCreate,
    ProjectPatch,
    RequirementCreate,
    RequirementPatch,
    ReorderInput,
    StageInput,
    TaskCreate,
    TaskPatch,
    WorkReportCreate,
    WorkReportPatch,
)

DEFAULT_STAGES = (
    ("立项", "#888780", True),
    ("迭代", "#378ADD", True),
    ("完结", "#639922", False),
)


def save_quick_capture(content: str, *, root: Path | None = None) -> tuple[str, datetime]:
    """Persist a quick capture as Markdown in the configured export root."""
    captured = content.strip()
    if not captured:
        raise ValidationError("捕捉内容不能为空")
    now = datetime.now(UTC).astimezone()
    target = (root or Path(settings.quick_capture_dir)).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    filename = f"{now:%Y-%m-%d-%H%M%S}-快速捕捉.md"
    path = target / filename
    suffix = 1
    while path.exists():
        path = target / f"{now:%Y-%m-%d-%H%M%S}-快速捕捉-{suffix}.md"
        suffix += 1
    path.write_text(
        "---\n"
        "title: 快速捕捉\n"
        f"created_at: {now.isoformat()}\n"
        "source: SAG 个人工作台\n"
        "---\n\n"
        "# 快速捕捉\n\n"
        f"{captured}\n",
        encoding="utf-8",
    )
    return path.name, now


async def _project(session: AsyncSession, user_id: str, project_id: str) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user_id,
            Project.archived.is_(False),
        )
    )
    if project is None:
        raise NotFoundError("项目不存在或已归档")
    return project


async def _task(session: AsyncSession, user_id: str, task_id: str) -> Task:
    task = await session.scalar(
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(
            Task.id == task_id,
            Task.user_id == user_id,
            Project.archived.is_(False),
        )
    )
    if task is None:
        raise NotFoundError("任务不存在")
    return task


async def list_stages(session: AsyncSession, user_id: str) -> list[ProjectStage]:
    stages = list((await session.scalars(select(ProjectStage).where(ProjectStage.user_id == user_id).order_by(ProjectStage.sort_order, ProjectStage.created_at))).all())
    if stages:
        return stages
    return [ProjectStage(id=f"default-{i}", user_id=user_id, label=label, color=color, sort_order=i, has_input=has_input) for i, (label, color, has_input) in enumerate(DEFAULT_STAGES)]


async def replace_stages(session: AsyncSession, user_id: str, values: list[StageInput]) -> list[ProjectStage]:
    if not values:
        raise ValidationError("至少需要一个项目阶段")
    old = list((await session.scalars(select(ProjectStage).where(ProjectStage.user_id == user_id))).all())
    for stage in old:
        await session.delete(stage)
    stages = [ProjectStage(id=new_id(), user_id=user_id, label=value.label.strip(), color=value.color, sort_order=i, has_input=value.has_input) for i, value in enumerate(values)]
    session.add_all(stages)
    await session.commit()
    return stages


async def list_projects(session: AsyncSession, user_id: str, include_archived: bool = False) -> list[Project]:
    query = select(Project).where(Project.user_id == user_id)
    if not include_archived:
        query = query.where(Project.archived.is_(False))
    return list((await session.scalars(query.order_by(Project.sort_order, Project.created_at))).all())


async def project_counts(session: AsyncSession, project: Project) -> tuple[int, int]:
    total = await session.scalar(select(func.count()).select_from(Task).where(Task.project_id == project.id, Task.user_id == project.user_id)) or 0
    active = await session.scalar(select(func.count()).select_from(Task).where(Task.project_id == project.id, Task.user_id == project.user_id, Task.status.not_in(("done", "cancelled")))) or 0
    return int(total), int(active)


async def project_counts_for_user(
    session: AsyncSession,
    user_id: str,
    project_ids: list[str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Return all project task counts in one grouped query.

    The project workspace loads every project at once, so doing two count
    queries per project makes the initial request grow linearly with project
    count. A grouped aggregate keeps the response to one database roundtrip.
    """
    query = select(
        Task.project_id,
        func.count(Task.id),
        func.sum(case((Task.status.not_in(("done", "cancelled")), 1), else_=0)),
    ).join(Project, Project.id == Task.project_id).where(
        Task.user_id == user_id,
        Project.archived.is_(False),
    )
    if project_ids:
        query = query.where(Task.project_id.in_(project_ids))
    rows = await session.execute(query.group_by(Task.project_id))
    return {
        project_id: (int(total or 0), int(active or 0))
        for project_id, total, active in rows.all()
    }


async def create_project(session: AsyncSession, user_id: str, data: ProjectCreate) -> Project:
    name = data.name.strip()
    duplicate = await session.scalar(select(Project).where(Project.user_id == user_id, Project.name == name, Project.archived.is_(False)))
    if duplicate:
        raise ConflictError("同名项目已存在")
    project = Project(user_id=user_id, **{**data.model_dump(), "name": name})
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def update_project(session: AsyncSession, user_id: str, project_id: str, data: ProjectPatch) -> Project:
    project = await _project(session, user_id, project_id)
    patch = data.model_dump(exclude_unset=True)
    expected = patch.pop("version", None)
    if expected is not None and expected != project.version:
        raise ConflictError("项目已被其他客户端更新，请刷新后重试")
    if "name" in patch:
        patch["name"] = patch["name"].strip()
        duplicate = await session.scalar(select(Project).where(Project.user_id == user_id, Project.name == patch["name"], Project.id != project_id, Project.archived.is_(False)))
        if duplicate:
            raise ConflictError("同名项目已存在")
    for key, value in patch.items():
        setattr(project, key, value)
    if project.start_date and project.end_date and project.end_date < project.start_date:
        raise ValidationError("项目结束日期必须不早于开始日期")
    project.version += 1
    await session.commit()
    await session.refresh(project)
    return project


async def delete_project(session: AsyncSession, user_id: str, project_id: str) -> None:
    project = await _project(session, user_id, project_id)
    project.archived = True
    project.version += 1
    await session.commit()


async def list_tasks(session: AsyncSession, user_id: str, project_id: str | None = None) -> list[Task]:
    query = select(Task).join(Project, Project.id == Task.project_id).where(
        Task.user_id == user_id,
        Project.archived.is_(False),
    )
    if project_id:
        await _project(session, user_id, project_id)
        query = query.where(Task.project_id == project_id)
    return list((await session.scalars(query.order_by(Task.project_id, Task.sort_order, Task.start_date, Task.created_at))).all())


async def daily_nodes(session: AsyncSession, task_ids: list[str]) -> dict[str, list[TaskDailyNode]]:
    if not task_ids:
        return {}
    rows = list((await session.scalars(select(TaskDailyNode).where(TaskDailyNode.task_id.in_(task_ids)).order_by(TaskDailyNode.node_date))).all())
    output: dict[str, list[TaskDailyNode]] = {}
    for row in rows:
        output.setdefault(row.task_id, []).append(row)
    return output


async def create_task(session: AsyncSession, user_id: str, data: TaskCreate) -> Task:
    await _project(session, user_id, data.project_id)
    if data.requirement_id:
        await _requirement(session, user_id, data.requirement_id)
    if data.parent_id:
        parent = await _task(session, user_id, data.parent_id)
        if parent.project_id != data.project_id:
            raise ValidationError("父任务必须属于同一项目")
    task = Task(user_id=user_id, **{**data.model_dump(), "title": data.title.strip()})
    if task.status == "done":
        task.complete_time = datetime.now(UTC)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def update_task(session: AsyncSession, user_id: str, task_id: str, data: TaskPatch) -> Task:
    task = await _task(session, user_id, task_id)
    patch = data.model_dump(exclude_unset=True)
    expected = patch.pop("version", None)
    if expected is not None and expected != task.version:
        raise ConflictError("任务已被其他客户端更新，请刷新后重试")
    if "project_id" in patch:
        project_id = (patch["project_id"] or "").strip()
        if not project_id:
            raise ValidationError("项目不能为空")
        patch["project_id"] = project_id
        await _project(session, user_id, project_id)
        if task.parent_id and project_id != task.project_id:
            task.parent_id = None
    if "requirement_id" in patch and patch["requirement_id"]:
        await _requirement(session, user_id, patch["requirement_id"])
    if "parent_id" in patch and patch["parent_id"]:
        parent = await _task(session, user_id, patch["parent_id"])
        target_project = patch.get("project_id", task.project_id)
        if parent.id == task.id or parent.project_id != target_project:
            raise ValidationError("父任务必须属于同一项目且不能是自身")
        # Walk the proposed ancestor chain so a deep edit cannot introduce a cycle.
        seen = {task.id}
        cursor: Task | None = parent
        while cursor is not None and cursor.parent_id:
            if cursor.parent_id in seen:
                raise ValidationError("任务层级不能形成循环")
            seen.add(cursor.parent_id)
            cursor = await _task(session, user_id, cursor.parent_id)
    old_status = task.status
    for key, value in patch.items():
        setattr(task, key, value)
    if task.start_date and task.due_date and task.due_date < task.start_date:
        raise ValidationError("任务截止日期必须不早于开始日期")
    if task.status == "done" and old_status != "done":
        task.complete_time = datetime.now(UTC)
    elif task.status != "done" and old_status == "done":
        task.complete_time = None
    task.version += 1
    await session.commit()
    await session.refresh(task)
    return task


async def delete_task(session: AsyncSession, user_id: str, task_id: str) -> None:
    """Cancel instead of hard deleting so reports retain their source record."""
    task = await _task(session, user_id, task_id)
    task.status = "cancelled"
    task.complete_time = None
    task.version += 1
    await session.commit()


async def upsert_daily_node(session: AsyncSession, user_id: str, task_id: str, node_date: date, data: DailyNodeInput) -> TaskDailyNode:
    task = await _task(session, user_id, task_id)
    node = await session.scalar(select(TaskDailyNode).where(TaskDailyNode.task_id == task.id, TaskDailyNode.node_date == node_date))
    if node is None:
        node = TaskDailyNode(task_id=task.id, node_date=node_date, **data.model_dump())
        session.add(node)
    else:
        node.state = data.state
        node.note = data.note
    await session.commit()
    await session.refresh(node)
    return node


async def reorder_tasks(session: AsyncSession, user_id: str, data: ReorderInput) -> list[Task]:
    project_id = data.project_id.strip()
    if not project_id:
        raise ValidationError("项目不能为空")
    await _project(session, user_id, project_id)
    ids = list(dict.fromkeys(data.task_ids))
    rows = list((await session.scalars(
        select(Task).where(
            Task.user_id == user_id,
            Task.project_id == project_id,
            Task.id.in_(ids),
        )
    )).all())
    if len(rows) != len(ids):
        raise ValidationError("只能排序当前项目内的任务")
    tasks = {task.id: task for task in rows}
    for index, task_id in enumerate(ids):
        task = tasks[task_id]
        task.sort_order = index
        task.version += 1
    await session.commit()
    return [tasks[task_id] for task_id in ids if task_id in tasks]


async def _requirement(session: AsyncSession, user_id: str, requirement_id: str) -> Requirement:
    requirement = await session.scalar(select(Requirement).where(Requirement.id == requirement_id, Requirement.user_id == user_id))
    if requirement is None:
        raise NotFoundError("需求不存在")
    return requirement


async def list_requirements(session: AsyncSession, user_id: str) -> list[Requirement]:
    return list((await session.scalars(select(Requirement).where(Requirement.user_id == user_id).order_by(Requirement.updated_at.desc()))).all())


async def requirement_counts(session: AsyncSession, user_id: str, requirement_id: str) -> tuple[int, int]:
    rows = list((await session.scalars(select(Task).where(Task.user_id == user_id, Task.requirement_id == requirement_id))).all())
    return len(rows), sum(task.status == "done" for task in rows)


async def create_requirement(session: AsyncSession, user_id: str, data: RequirementCreate) -> Requirement:
    if data.project_id:
        await _project(session, user_id, data.project_id)
    requirement = Requirement(user_id=user_id, **{**data.model_dump(), "title": data.title.strip()})
    session.add(requirement)
    await session.commit()
    await session.refresh(requirement)
    return requirement


async def update_requirement(session: AsyncSession, user_id: str, requirement_id: str, data: RequirementPatch) -> Requirement:
    requirement = await _requirement(session, user_id, requirement_id)
    patch = data.model_dump(exclude_unset=True)
    expected = patch.pop("version", None)
    if expected is not None and expected != requirement.version:
        raise ConflictError("需求已被其他客户端更新，请刷新后重试")
    if patch.get("project_id"):
        await _project(session, user_id, patch["project_id"])
    if patch.get("status") in {"rejected", "shelved"} and not (patch.get("decision_reason") or requirement.decision_reason).strip():
        raise ValidationError("拒绝或搁置需求必须填写原因")
    for key, value in patch.items():
        setattr(requirement, key, value)
    if requirement.status == "converted" and requirement.converted_at is None:
        requirement.converted_at = datetime.now(UTC)
    requirement.version += 1
    await session.commit()
    await session.refresh(requirement)
    return requirement


async def convert_requirement(session: AsyncSession, user_id: str, requirement_id: str, project_id: str, titles: list[str]) -> list[Task]:
    requirement = await _requirement(session, user_id, requirement_id)
    await _project(session, user_id, project_id)
    clean_titles = [title.strip() for title in titles if title.strip()]
    if not clean_titles:
        raise ValidationError("至少需要一个任务标题")
    existing = await session.scalar(select(func.count()).select_from(Task).where(Task.project_id == project_id, Task.user_id == user_id)) or 0
    tasks = [Task(user_id=user_id, project_id=project_id, title=title, requirement_id=requirement.id, sort_order=int(existing) + index) for index, title in enumerate(clean_titles)]
    session.add_all(tasks)
    requirement.project_id = project_id
    requirement.status = "converted"
    requirement.converted_at = datetime.now(UTC)
    requirement.version += 1
    await session.commit()
    for task in tasks:
        await session.refresh(task)
    return tasks


def _report_snapshot(tasks: list[Task], projects: list[Project], requirements: list[Requirement], report_type: str, report_date: date) -> dict:
    project_names = {project.id: project.name for project in projects}
    day = report_date.isoformat()
    if report_type == "daily":
        selected = [task for task in tasks if (task.complete_time and task.complete_time.astimezone().date() == report_date) or (task.created_at and task.created_at.astimezone().date() == report_date)]
    else:
        end = report_date + timedelta(days=6)
        selected = [task for task in tasks if (task.complete_time and report_date <= task.complete_time.astimezone().date() <= end) or (task.created_at and report_date <= task.created_at.astimezone().date() <= end)]
    return {"report_type": report_type, "report_date": day, "tasks": [{"id": task.id, "title": task.title, "status": task.status, "project_id": task.project_id, "project_name": project_names.get(task.project_id, "未知项目"), "due_date": task.due_date.isoformat() if task.due_date else None} for task in selected], "requirements": [{"id": item.id, "title": item.title, "status": item.status} for item in requirements if item.updated_at.astimezone().date() == report_date]}


async def generate_report(session: AsyncSession, user_id: str, data: WorkReportCreate) -> WorkReport:
    tasks = list((await session.scalars(select(Task).join(Project, Project.id == Task.project_id).where(Task.user_id == user_id, Project.archived.is_(False)))).all())
    projects = await list_projects(session, user_id)
    requirements = await list_requirements(session, user_id)
    snapshot = _report_snapshot(tasks, projects, requirements, data.report_type, data.report_date)
    report = await session.scalar(select(WorkReport).where(WorkReport.user_id == user_id, WorkReport.report_type == data.report_type, WorkReport.report_date == data.report_date, WorkReport.status == "draft"))
    if report is None:
        report = WorkReport(user_id=user_id, report_type=data.report_type, report_date=data.report_date, snapshot=snapshot, status="generated")
        session.add(report)
    else:
        report.snapshot = snapshot
        report.status = "generated"
        report.version += 1
    await session.commit()
    await session.refresh(report)
    return report


async def list_reports(session: AsyncSession, user_id: str) -> list[WorkReport]:
    return list((await session.scalars(select(WorkReport).where(WorkReport.user_id == user_id).order_by(WorkReport.report_date.desc(), WorkReport.created_at.desc()))).all())


async def update_report(session: AsyncSession, user_id: str, report_id: str, data: WorkReportPatch) -> WorkReport:
    report = await session.scalar(select(WorkReport).where(WorkReport.id == report_id, WorkReport.user_id == user_id))
    if report is None:
        raise NotFoundError("报告不存在")
    if report.status in {"confirmed", "archived"}:
        raise ConflictError("已确认报告不可修改")
    patch = data.model_dump(exclude_unset=True)
    expected = patch.pop("version", None)
    if expected is not None and expected != report.version:
        raise ConflictError("报告已被其他客户端更新，请刷新后重试")
    for key, value in patch.items():
        setattr(report, key, value)
    report.status = "draft"
    report.version += 1
    await session.commit()
    await session.refresh(report)
    return report


async def confirm_report(session: AsyncSession, user_id: str, report_id: str) -> WorkReport:
    report = await session.scalar(select(WorkReport).where(WorkReport.id == report_id, WorkReport.user_id == user_id))
    if report is None:
        raise NotFoundError("报告不存在")
    report.status = "confirmed"
    report.confirmed_at = datetime.now(UTC)
    report.version += 1
    await session.commit()
    await session.refresh(report)
    return report


async def analytics(session: AsyncSession, user_id: str, start_date: date, end_date: date) -> dict:
    projects = await list_projects(session, user_id)
    tasks = list((await session.scalars(select(Task).join(Project, Project.id == Task.project_id).where(Task.user_id == user_id, Project.archived.is_(False)))).all())
    requirements = await list_requirements(session, user_id)
    selected = [task for task in tasks if (task.created_at.astimezone().date() <= end_date and (task.due_date is None or task.due_date >= start_date))]
    active = [task for task in selected if task.status != "cancelled"]
    completed = [task for task in active if task.status == "done"]
    overdue = [task for task in active if task.status != "done" and task.due_date and task.due_date < end_date]
    statuses = {status: sum(task.status == status for task in selected) for status in ("todo", "in-progress", "blocked", "done", "cancelled")}
    project_completion = []
    for project in projects:
        rows = [task for task in active if task.project_id == project.id]
        done = sum(task.status == "done" for task in rows)
        project_completion.append({"id": project.id, "name": project.name, "total": len(rows), "done": done, "rate": round(done / len(rows) * 100) if rows else 0})
    daily = []
    cursor = start_date
    while cursor <= end_date:
        daily.append({"date": cursor.isoformat(), "created": sum(task.created_at.astimezone().date() == cursor for task in selected), "completed": sum(task.complete_time and task.complete_time.astimezone().date() == cursor for task in completed)})
        cursor += timedelta(days=1)
    converted = sum(item.status in {"converted", "in-progress", "done"} for item in requirements if item.created_at.astimezone().date() <= end_date)
    return {"start_date": start_date, "end_date": end_date, "total_tasks": len(active), "completed_tasks": len(completed), "cancelled_tasks": len(selected) - len(active), "overdue_tasks": len(overdue), "completion_rate": round(len(completed) / len(active) * 100) if active else 0, "status_distribution": statuses, "project_completion": project_completion, "daily_trend": daily, "requirement_total": len(requirements), "requirement_converted": converted}
