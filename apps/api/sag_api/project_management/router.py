from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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
    RequirementCreate,
    RequirementOut,
    RequirementPatch,
    QuickCaptureCreate,
    QuickCaptureOut,
    ReorderInput,
    SnapshotOut,
    StageInput,
    StageOut,
    TaskCreate,
    TaskOut,
    TaskPatch,
    AnalyticsOut,
    WorkReportCreate,
    WorkReportOut,
    WorkReportPatch,
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


@router.post("/quick-captures", response_model=QuickCaptureOut, status_code=201)
async def create_quick_capture(body: QuickCaptureCreate, user: User = Depends(get_current_user)) -> QuickCaptureOut:
    # The caller is authenticated, but it never supplies a path or filename.
    del user
    filename, created_at = service.save_quick_capture(body.content)
    return QuickCaptureOut(filename=filename, created_at=created_at)


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


def _requirement_out(requirement, counts: tuple[int, int]) -> RequirementOut:
    fields = {name: getattr(requirement, name) for name in RequirementOut.model_fields if hasattr(requirement, name)}
    fields.update(task_count=counts[0], completed_task_count=counts[1])
    return RequirementOut.model_validate(fields)


@router.get("/requirements", response_model=list[RequirementOut])
async def requirements(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[RequirementOut]:
    values = await service.list_requirements(session, user.id)
    return [_requirement_out(item, await service.requirement_counts(session, user.id, item.id)) for item in values]


@router.post("/requirements", response_model=RequirementOut, status_code=201)
async def create_requirement(body: RequirementCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> RequirementOut:
    item = await service.create_requirement(session, user.id, body)
    return _requirement_out(item, (0, 0))


@router.patch("/requirements/{requirement_id}", response_model=RequirementOut)
async def update_requirement(requirement_id: str, body: RequirementPatch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> RequirementOut:
    item = await service.update_requirement(session, user.id, requirement_id, body)
    return _requirement_out(item, await service.requirement_counts(session, user.id, item.id))


@router.post("/requirements/{requirement_id}/convert", response_model=list[TaskOut], status_code=201)
async def convert_requirement(requirement_id: str, project_id: str, titles: list[str], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[TaskOut]:
    return await _task_outs(session, await service.convert_requirement(session, user.id, requirement_id, project_id, titles))


@router.get("/reports", response_model=list[WorkReportOut])
async def reports(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[WorkReportOut]:
    return [WorkReportOut.model_validate(item) for item in await service.list_reports(session, user.id)]


@router.post("/reports/generate", response_model=WorkReportOut, status_code=201)
async def generate_report(body: WorkReportCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> WorkReportOut:
    return WorkReportOut.model_validate(await service.generate_report(session, user.id, body))


@router.patch("/reports/{report_id}", response_model=WorkReportOut)
async def update_report(report_id: str, body: WorkReportPatch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> WorkReportOut:
    return WorkReportOut.model_validate(await service.update_report(session, user.id, report_id, body))


@router.post("/reports/{report_id}/confirm", response_model=WorkReportOut)
async def confirm_report(report_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> WorkReportOut:
    return WorkReportOut.model_validate(await service.confirm_report(session, user.id, report_id))


@router.get("/analytics", response_model=AnalyticsOut)
async def analytics(start_date: date | None = None, end_date: date | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> AnalyticsOut:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=29))
    return AnalyticsOut.model_validate(await service.analytics(session, user.id, start, end))


@router.put("/tasks/{task_id}/daily-node/{node_date}", response_model=DailyNodeOut)
async def daily_node(task_id: str, node_date: date, body: DailyNodeInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> DailyNodeOut:
    return DailyNodeOut.model_validate(await service.upsert_daily_node(session, user.id, task_id, node_date, body))


@router.post("/tasks/reorder", response_model=list[TaskOut])
async def reorder(body: ReorderInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[TaskOut]:
    return await _task_outs(session, await service.reorder_tasks(session, user.id, body))
