from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.db.models import User
from sag_api.project_management import service
from sag_api.project_management.schemas import (
    DailyNodeInput,
    DailyNodeOut,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    ReorderInput,
    SnapshotOut,
    StageInput,
    StageOut,
    TaskCreate,
    TaskOut,
    TaskPatch,
)

router = APIRouter(prefix="/project-management", tags=["project-management"])


def _project_out(project, counts: tuple[int, int]) -> ProjectOut:
    fields = {name: getattr(project, name) for name in ProjectOut.model_fields if hasattr(project, name)}
    fields.update(task_count=counts[0], active_count=counts[1])
    return ProjectOut.model_validate(fields)


async def _task_outs(session: AsyncSession, tasks) -> list[TaskOut]:
    nodes = await service.daily_nodes(session, [task.id for task in tasks])
    output: list[TaskOut] = []
    for task in tasks:
        # SQLAlchemy may expire an onupdate column after a bulk commit even
        # with expire_on_commit disabled. Refresh before building a dict so
        # reorder responses remain as complete as normal CRUD responses.
        if "updated_at" not in task.__dict__:
            await session.refresh(task)
        fields = {name: getattr(task, name) for name in TaskOut.model_fields if hasattr(task, name)}
        fields["daily_nodes"] = nodes.get(task.id, [])
        output.append(TaskOut.model_validate(fields))
    return output


@router.get("/snapshot", response_model=SnapshotOut)
async def snapshot(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> SnapshotOut:
    projects = await service.list_projects(session, user.id)
    tasks = await service.list_tasks(session, user.id)
    counts = await service.project_counts_for_user(session, user.id, [project.id for project in projects])
    project_out = [_project_out(project, counts.get(project.id, (0, 0))) for project in projects]
    return SnapshotOut(projects=project_out, tasks=await _task_outs(session, tasks), stages=[StageOut.model_validate(stage) for stage in await service.list_stages(session, user.id)], generated_at=datetime.now(timezone.utc))


@router.get("/stages", response_model=list[StageOut])
async def stages(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[StageOut]:
    return [StageOut.model_validate(stage) for stage in await service.list_stages(session, user.id)]


@router.put("/stages", response_model=list[StageOut])
async def replace_stages(values: list[StageInput], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[StageOut]:
    return [StageOut.model_validate(stage) for stage in await service.replace_stages(session, user.id, values)]


@router.get("/projects", response_model=list[ProjectOut])
async def projects(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    values = await service.list_projects(session, user.id)
    counts = await service.project_counts_for_user(session, user.id, [project.id for project in values])
    return [_project_out(project, counts.get(project.id, (0, 0))) for project in values]


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> ProjectOut:
    project = await service.create_project(session, user.id, body)
    return _project_out(project, (0, 0))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, body: ProjectPatch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> ProjectOut:
    project = await service.update_project(session, user.id, project_id, body)
    return _project_out(project, await service.project_counts(session, project))


@router.delete("/projects/{project_id}")
async def archive_project(project_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    await service.delete_project(session, user.id, project_id)
    return {"ok": True}


@router.get("/tasks", response_model=list[TaskOut])
async def tasks(project_id: str | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[TaskOut]:
    return await _task_outs(session, await service.list_tasks(session, user.id, project_id))


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> TaskOut:
    return (await _task_outs(session, [await service.create_task(session, user.id, body)]))[0]


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(task_id: str, body: TaskPatch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> TaskOut:
    return (await _task_outs(session, [await service.update_task(session, user.id, task_id, body)]))[0]


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    await service.delete_task(session, user.id, task_id)
    return {"ok": True}


@router.put("/tasks/{task_id}/daily-node/{node_date}", response_model=DailyNodeOut)
async def daily_node(task_id: str, node_date: date, body: DailyNodeInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> DailyNodeOut:
    return DailyNodeOut.model_validate(await service.upsert_daily_node(session, user.id, task_id, node_date, body))


@router.post("/tasks/reorder", response_model=list[TaskOut])
async def reorder(body: ReorderInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[TaskOut]:
    return await _task_outs(session, await service.reorder_tasks(session, user.id, body))
