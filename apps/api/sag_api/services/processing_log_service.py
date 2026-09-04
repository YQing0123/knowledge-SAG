"""Persistent, bounded processing logs shown from each knowledge source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.db.models import ProcessingLog


async def record_processing_log(
    session: AsyncSession,
    *,
    source_id: str,
    stage: str,
    message: str,
    level: str = "info",
    document_id: str | None = None,
    job_id: str | None = None,
    filename: str | None = None,
) -> None:
    session.add(
        ProcessingLog(
            source_id=source_id,
            document_id=document_id,
            job_id=job_id,
            filename=filename,
            stage=stage[:32],
            level=level[:16],
            message=message[:2000],
        )
    )
    await session.commit()


async def list_processing_logs(
    session: AsyncSession,
    source_id: str,
    *,
    document_id: str | None = None,
    level: str | None = None,
    limit: int = 200,
) -> list[ProcessingLog]:
    statement = select(ProcessingLog).where(ProcessingLog.source_id == source_id)
    if document_id:
        statement = statement.where(ProcessingLog.document_id == document_id)
    if level:
        statement = statement.where(ProcessingLog.level == level)
    rows = await session.scalars(
        statement.order_by(ProcessingLog.created_at.desc()).limit(limit)
    )
    return list(rows)


async def cleanup_processing_logs(
    session_factory: async_sessionmaker,
    retention_days: int,
) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, retention_days))
    async with session_factory() as session:
        result = await session.execute(
            delete(ProcessingLog).where(ProcessingLog.created_at < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)


class ProcessingLogRetentionScheduler:
    """Daily retention cleanup; the configured value is a keep-days policy."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="sag-processing-log-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                from sag_api.services.settings_service import processing_log_retention_days

                async with self._session_factory() as session:
                    retention_days = await processing_log_retention_days(session)
                await cleanup_processing_logs(self._session_factory, retention_days)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Retention cleanup is optional and must not impact ingestion.
                pass
            await asyncio.sleep(24 * 60 * 60)
