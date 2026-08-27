from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.db.base import new_id
from sag_api.project_management.models import Project, ProjectStage, Task, TaskDailyNode
from sag_api.project_management.schemas import (
    DailyNodeInput,
    ProjectCreate,
    ProjectPatch,
    ReorderInput,
    StageInput,
    TaskCreate,
    TaskPatch,
)

DEFAULT_STAGES = (
    ("立项", "#888780", True),
    ("规划", "#378ADD", True),
    ("开发", "#185FA5", True),
    ("测试", "#BA7517", True),
    ("上线", "#639922", False),
)


async def _project(session: AsyncSession, user_id: str, project_id: str) -> Project:
    project = await session.scalar(select(Project).where(Project.id == project_id, Project.user_id == user_id))
    if project is None:
        raise NotFoundError("项目不存在")
    return project


async def _task(session: AsyncSession, user_id: str, task_id: str) -> Task:
    task = await session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
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
    ).where(Task.user_id == user_id)
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
    query = select(Task).where(Task.user_id == user_id)
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
    if "project_id" in patch and patch["project_id"]:
        await _project(session, user_id, patch["project_id"])
        if task.parent_id and patch["project_id"] != task.project_id:
            task.parent_id = None
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
    task = await _task(session, user_id, task_id)
    await session.delete(task)
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
    ids = list(dict.fromkeys(data.task_ids))
    rows = list((await session.scalars(select(Task).where(Task.user_id == user_id, Task.id.in_(ids)))).all())
    tasks = {task.id: task for task in rows}
    for index, task_id in enumerate(ids):
        task = tasks.get(task_id)
        if task is not None:
            task.sort_order = index
            task.version += 1
    await session.commit()
    return [tasks[task_id] for task_id in ids if task_id in tasks]
