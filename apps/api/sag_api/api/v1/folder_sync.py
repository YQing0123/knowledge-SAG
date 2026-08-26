from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_job_queue
from sag_api.db.models import User
from sag_api.jobs import JobQueue
from sag_api.schemas.folder_sync import (
    FolderSyncConfigOut,
    FolderSyncConfigUpdate,
    FolderSyncDirectoryListOut,
    FolderSyncRunOut,
)
from sag_api.services.folder_sync_service import (
    enqueue_folder_sync,
    get_folder_sync_config,
    get_folder_sync_public,
    list_sync_directories,
    update_folder_sync_config,
)

router = APIRouter(prefix="/sources/{source_id}/folder-sync", tags=["folder-sync"])


@router.get("", response_model=FolderSyncConfigOut)
async def get_config(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FolderSyncConfigOut:
    return await get_folder_sync_public(session, source_id)


@router.put("", response_model=FolderSyncConfigOut)
async def save_config(
    source_id: str,
    body: FolderSyncConfigUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FolderSyncConfigOut:
    return await update_folder_sync_config(session, source_id, body)


@router.get("/directories", response_model=FolderSyncDirectoryListOut)
async def directories(
    source_id: str,
    path: str = Query(default="", max_length=512),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FolderSyncDirectoryListOut:
    await get_folder_sync_config(session, source_id)
    return list_sync_directories(path)


@router.post("/run", response_model=FolderSyncRunOut)
async def run(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> FolderSyncRunOut:
    job = await enqueue_folder_sync(session, source_id, job_queue)
    return FolderSyncRunOut(job_id=job.id, status=job.status.value)
