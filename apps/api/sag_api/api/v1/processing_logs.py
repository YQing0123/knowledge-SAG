from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import NotFoundError
from sag_api.db.models import Source, User
from sag_api.schemas.processing_log import ProcessingLogOut
from sag_api.services.processing_log_service import list_processing_logs

router = APIRouter(prefix="/sources/{source_id}/processing-logs", tags=["processing-logs"])


@router.get("", response_model=list[ProcessingLogOut])
async def list_logs(
    source_id: str,
    document_id: str | None = Query(default=None, max_length=64),
    level: str | None = Query(default=None, pattern="^(info|warning|error)$"),
    limit: int = Query(default=200, ge=1, le=500),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProcessingLogOut]:
    # Avoid exposing a guessed source ID even when no document log exists yet.
    if await session.get(Source, source_id) is None:
        raise NotFoundError("信源不存在")
    rows = await list_processing_logs(
        session, source_id, document_id=document_id, level=level, limit=limit
    )
    return [ProcessingLogOut.model_validate(row) for row in rows]
