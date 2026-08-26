"""Incremental, confined local-folder synchronization for any knowledge source."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import mimetypes
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.core.config import Settings, settings
from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.db.models import Document, FolderSyncConfig, FolderSyncEntry, Job, Source
from sag_api.enums import JobStatus, JobType
from sag_api.jobs import JobQueue
from sag_api.schemas.folder_sync import (
    FolderSyncConfigOut,
    FolderSyncConfigUpdate,
    FolderSyncDirectory,
    FolderSyncDirectoryListOut,
)
from sag_api.services.document_service import create_document_from_upload, delete_document
from sag_api.services.markdown_image_service import enrich_markdown, fingerprint_for

log = get_logger("folder-sync")


def _now() -> datetime:
    return datetime.now(UTC)


def _root(config: Settings = settings) -> Path:
    return Path(config.folder_sync_root).expanduser().resolve()


def resolve_sync_directory(directory: str, config: Settings = settings) -> Path:
    """Resolve only a relative child of the configured container mount root."""
    raw = directory.strip().replace("\\", "/")
    if raw.startswith("/"):
        raise ValidationError("同步目录必须是挂载根目录下的相对路径，且不能包含 ..")
    normalized = raw.strip("/")
    candidate = PurePosixPath(normalized)
    if not normalized or candidate.is_absolute() or ".." in candidate.parts:
        raise ValidationError("同步目录必须是挂载根目录下的相对路径，且不能包含 ..")
    root = _root(config)
    path = (root / Path(*candidate.parts)).resolve()
    if root != path and root not in path.parents:
        raise ValidationError("同步目录超出允许的挂载根目录")
    return path


def list_sync_directories(directory: str, config: Settings = settings) -> FolderSyncDirectoryListOut:
    """List immediate non-symlink directories inside the read-only sync mount."""
    root = _root(config)
    if not root.is_dir():
        return FolderSyncDirectoryListOut(
            current_path=".",
            parent_path=None,
            directories=[],
            mount_root=str(root),
            mount_available=False,
        )
    requested = directory.strip().replace("\\", "/").strip("/")
    if requested in ("", "."):
        path = root
        current_path = "."
    else:
        path = resolve_sync_directory(requested, config)
        if not path.is_dir():
            raise ValidationError("选择的同步目录不存在或不可读取")
        current_path = requested
    directories = [
        FolderSyncDirectory(
            name=entry.name,
            path=(PurePosixPath(current_path) / entry.name).as_posix(),
        )
        for entry in sorted(path.iterdir(), key=lambda item: item.name.casefold())
        if entry.is_dir() and not entry.is_symlink()
    ]
    parent_path = None
    if current_path != ".":
        parent = PurePosixPath(current_path).parent.as_posix()
        parent_path = "." if parent == "." else parent
    return FolderSyncDirectoryListOut(
        current_path=current_path,
        parent_path=parent_path,
        directories=directories,
        mount_root=str(root),
        mount_available=True,
    )


def _matches(relative_path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    path = PurePosixPath(relative_path)

    def match(pattern: str) -> bool:
        # ``**/*.md`` is commonly used for recursive Markdown matching. pathlib
        # does not match it against a file at the configured folder root, so also
        # test the equivalent root-level pattern.
        root_pattern = pattern[3:] if pattern.startswith("**/") else pattern
        return (
            path.match(pattern)
            or fnmatch.fnmatchcase(relative_path, pattern)
            or fnmatch.fnmatchcase(relative_path, root_pattern)
        )

    return any(match(pattern) for pattern in include_globs) and not any(match(pattern) for pattern in exclude_globs)


def _configured_directories(config: FolderSyncConfig) -> list[str]:
    values = config.directories if isinstance(config.directories, list) else []
    if not values and config.directory:
        values = [config.directory]
    result: list[str] = []
    for value in values:
        normalized = str(value).strip().replace("\\", "/").strip("/")
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _directory_for_relative(relative: str, config: FolderSyncConfig) -> str:
    candidates = _configured_directories(config)
    matching = [item for item in candidates if item == "." or relative == item or relative.startswith(f"{item}/")]
    return max(matching, key=len) if matching else "."


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scan_files(config: FolderSyncConfig, app_settings: Settings) -> list[tuple[str, Path, os.stat_result, str]]:
    results: list[tuple[str, Path, os.stat_result, str]] = []
    directories = _configured_directories(config)
    if not directories:
        raise ValidationError("请至少添加一个同步文件夹")
    seen_paths: set[Path] = set()
    for directory_label in directories:
        directory = resolve_sync_directory(directory_label, app_settings)
        if not directory.is_dir():
            raise ValidationError(f"同步目录不存在或不可读取：{directory_label}")
        root = directory.resolve()
        prefix = "" if directory_label == "." else directory_label
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not Path(current, name).is_symlink()]
            for name in names:
                path = Path(current, name)
                if path.is_symlink() or not path.is_file():
                    continue
                resolved_path = path.resolve()
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                local_relative = path.relative_to(root).as_posix()
                relative = f"{prefix}/{local_relative}" if prefix else local_relative
                if not _matches(local_relative, list(config.include_globs or []), list(config.exclude_globs or [])):
                    continue
                stat = path.stat()
                if stat.st_size <= 0:
                    continue
                if stat.st_size > app_settings.max_upload_mb * 1024 * 1024:
                    log.warning("跳过超过上传上限的同步文件 source=%s path=%s", config.source_id, relative)
                    continue
                results.append((relative, path, stat, _hash_file(path)))
    return sorted(results, key=lambda item: item[0])


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _public(config: FolderSyncConfig, app_settings: Settings = settings) -> FolderSyncConfigOut:
    root = _root(app_settings)
    return FolderSyncConfigOut(
        source_id=config.source_id,
        enabled=config.enabled,
        directory=(_configured_directories(config) or [""])[0],
        directories=_configured_directories(config),
        mode=config.mode,
        interval_seconds=config.interval_seconds,
        schedule_time=config.schedule_time,
        weekday=config.weekday,
        include_globs=list(config.include_globs or []),
        exclude_globs=list(config.exclude_globs or []),
        sync_deletions=config.sync_deletions,
        image_enrichment_enabled=config.image_enrichment_enabled,
        image_enrichment_mode=config.image_enrichment_mode,
        image_max_count=config.image_max_count,
        image_max_mb=config.image_max_mb,
        current_stage=config.current_stage,
        current_file=config.current_file,
        last_started_at=config.last_started_at,
        last_finished_at=config.last_finished_at,
        last_error=config.last_error,
        last_summary=dict(config.last_summary or {}),
        mount_root=str(root),
        mount_available=root.is_dir(),
        timezone=app_settings.timezone,
        image_enrichment_available=app_settings.mineru_provider == "official" and app_settings.mineru_configured,
    )


async def get_folder_sync_config(session: AsyncSession, source_id: str) -> FolderSyncConfig:
    if await session.get(Source, source_id) is None:
        raise NotFoundError("信源不存在")
    config = await session.get(FolderSyncConfig, source_id)
    if config is None:
        config = FolderSyncConfig(source_id=source_id)
        session.add(config)
        await session.commit()
        await session.refresh(config)
    return config


async def get_folder_sync_public(session: AsyncSession, source_id: str) -> FolderSyncConfigOut:
    return _public(await get_folder_sync_config(session, source_id))


async def update_folder_sync_config(
    session: AsyncSession, source_id: str, patch: FolderSyncConfigUpdate
) -> FolderSyncConfigOut:
    config = await get_folder_sync_config(session, source_id)
    if patch.image_enrichment_enabled and (
        settings.mineru_provider != "official" or not settings.mineru_configured
    ):
        raise ValidationError("图片采集需要先在 SAG 设置中配置 MinerU 官方服务与 API Key")
    directories = (
        patch.directories
        if "directories" in patch.model_fields_set
        else ([patch.directory] if patch.directory else [])
    )
    if patch.enabled:
        if not directories:
            raise ValidationError("请至少添加一个同步文件夹")
        for directory in directories:
            resolve_sync_directory(directory)
    config.enabled = patch.enabled
    config.directories = directories
    config.directory = directories[0] if directories else ""
    config.mode = patch.mode
    config.interval_seconds = patch.interval_seconds
    config.schedule_time = patch.schedule_time
    config.weekday = patch.weekday
    config.include_globs = patch.include_globs or ["**/*.md", "*.md"]
    config.exclude_globs = patch.exclude_globs
    config.sync_deletions = patch.sync_deletions
    config.image_enrichment_enabled = patch.image_enrichment_enabled
    config.image_enrichment_mode = patch.image_enrichment_mode
    config.image_max_count = patch.image_max_count
    config.image_max_mb = patch.image_max_mb
    config.last_error = None
    await session.commit()
    await session.refresh(config)
    return _public(config)


async def enqueue_folder_sync(session: AsyncSession, source_id: str, job_queue: JobQueue) -> Job:
    config = await get_folder_sync_config(session, source_id)
    if not config.enabled:
        raise ValidationError("请先启用文件夹同步")
    if not _configured_directories(config):
        raise ValidationError("请至少添加一个同步文件夹")
    if not _root().is_dir():
        raise ValidationError("同步挂载目录不可用，请检查 SAG_SYNC_HOST_PATH 后重新部署")
    active = await session.scalar(
        select(Job)
        .where(
            Job.source_id == source_id,
            Job.type == JobType.SYNC_SOURCE,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if active is not None:
        return active
    job = Job(type=JobType.SYNC_SOURCE, source_id=source_id, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    from sag_api.services.document_service import _enqueue_persisted_job

    await _enqueue_persisted_job(job_queue, job.id)
    return job


async def run_folder_sync(
    session: AsyncSession,
    source: Source,
    job_queue: JobQueue,
    *,
    app_settings: Settings = settings,
) -> dict[str, int]:
    """Apply one manifest diff. Updated files get a fresh document then retire the old one."""
    config = await session.get(FolderSyncConfig, source.id)
    if config is None or not config.enabled:
        return {"discovered": 0, "created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0}
    config.last_started_at = _now()
    config.last_error = None
    config.current_stage = "scanning"
    config.current_file = None
    await session.commit()
    summary = {"discovered": 0, "created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0}
    try:
        scanned = await asyncio.to_thread(_scan_files, config, app_settings)
        summary["discovered"] = len(scanned)
        if len(scanned) > app_settings.folder_sync_max_files_per_run:
            raise ValidationError(
                f"本次发现 {len(scanned)} 个文件，超过单次同步上限 {app_settings.folder_sync_max_files_per_run}"
            )
        entries = list(
            (await session.scalars(select(FolderSyncEntry).where(FolderSyncEntry.source_id == source.id))).all()
        )
        by_path = {entry.relative_path: entry for entry in entries}
        seen: set[str] = set()
        for relative, path, stat, content_hash in scanned:
            seen.add(relative)
            entry = by_path.get(relative)
            raw_data = await asyncio.to_thread(path.read_bytes)
            try:
                raw_text = raw_data.decode("utf-8")
            except UnicodeDecodeError:
                raw_text = ""
            image_root = _root(app_settings)
            fingerprint, refs = (
                fingerprint_for(raw_text, path, image_root, content_hash, config, app_settings)
                if raw_text
                else (content_hash, [])
            )
            # Older manifests do not have a raw fingerprint yet. Their
            # unchanged file metadata is used once to backfill it, so toggling
            # image collection does not re-upload an entire existing folder.
            legacy_raw_match = (
                entry is not None
                and not entry.raw_content_hash
                and entry.size_bytes == stat.st_size
                and entry.mtime_ns == stat.st_mtime_ns
            )
            raw_file_is_current = entry is not None and entry.raw_content_hash == content_hash
            if raw_file_is_current or legacy_raw_match:
                existing_document = await session.get(Document, entry.document_id) if entry.document_id else None
                if existing_document is not None:
                    if not entry.raw_content_hash:
                        entry.raw_content_hash = content_hash
                        await session.commit()
                    summary["unchanged"] += 1
                    continue
            # A source may already contain documents manually uploaded from the
            # selected folder before folder sync is enabled. Adopt those files
            # into the manifest instead of creating duplicate documents. Image
            # enrichment intentionally bypasses adoption so it can generate the
            # requested MinerU supplement for the existing Markdown source.
            if entry is None and not config.image_enrichment_enabled:
                existing_document = await session.scalar(
                    select(Document)
                    .where(
                        Document.source_id == source.id,
                        Document.filename == path.name,
                        Document.is_active.is_(True),
                    )
                    .order_by(Document.created_at.desc())
                    .limit(1)
                )
                if existing_document is not None:
                    session.add(
                        FolderSyncEntry(
                            source_id=source.id,
                            relative_path=relative,
                            raw_content_hash=content_hash,
                            content_hash=fingerprint,
                            size_bytes=stat.st_size,
                            mtime_ns=stat.st_mtime_ns,
                            document_id=existing_document.id,
                            image_enrichment_status="not_requested",
                            image_count=0,
                        )
                    )
                    await session.commit()
                    summary["unchanged"] += 1
                    summary["adopted"] = summary.get("adopted", 0) + 1
                    continue
            if config.image_enrichment_enabled and refs:
                config.current_stage = "image_parsing"
                config.current_file = relative
                await session.commit()
            enrichment = await enrich_markdown(raw_data, path, image_root, relative, source.id, config, app_settings)
            if config.image_enrichment_enabled and refs:
                config.current_stage = "image_merging"
                await session.commit()
            document, _ = await create_document_from_upload(
                session,
                source,
                filename=relative,
                content_type=_content_type(path),
                data=enrichment.content,
                upload_dir=app_settings.upload_dir,
                job_queue=job_queue,
            )
            old_document_id = entry.document_id if entry else None
            if entry is None:
                entry = FolderSyncEntry(
                    source_id=source.id,
                    relative_path=relative,
                    raw_content_hash=content_hash,
                    content_hash=enrichment.fingerprint,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    document_id=document.id,
                    image_enrichment_status=enrichment.status,
                    image_enrichment_error="; ".join(enrichment.errors)[:2000] or None,
                    image_count=enrichment.enriched_count,
                )
                session.add(entry)
                summary["created"] += 1
            else:
                entry.raw_content_hash = content_hash
                entry.content_hash = enrichment.fingerprint
                entry.size_bytes = stat.st_size
                entry.mtime_ns = stat.st_mtime_ns
                entry.document_id = document.id
                entry.image_enrichment_status = enrichment.status
                entry.image_enrichment_error = "; ".join(enrichment.errors)[:2000] or None
                entry.image_count = enrichment.enriched_count
                summary["updated"] += 1
            await session.commit()
            if enrichment.enriched_count:
                summary["images"] = summary.get("images", 0) + enrichment.enriched_count
            if enrichment.errors:
                summary["image_failed"] = summary.get("image_failed", 0) + len(enrichment.errors)
            if old_document_id:
                try:
                    await delete_document(session, source, old_document_id, job_queue=job_queue)
                except (ConflictError, NotFoundError):
                    log.warning("旧同步文档将保留等待人工处理 source=%s document=%s", source.id, old_document_id)
        if config.sync_deletions:
            for entry in entries:
                if entry.relative_path in seen:
                    continue
                old_document_id = entry.document_id
                await session.delete(entry)
                await session.commit()
                if old_document_id:
                    try:
                        await delete_document(session, source, old_document_id, job_queue=job_queue)
                        summary["deleted"] += 1
                    except (ConflictError, NotFoundError):
                        log.warning("未能自动删除缺失文件对应文档 source=%s document=%s", source.id, old_document_id)
        config.last_summary = summary
        config.last_finished_at = _now()
        config.last_error = None
        config.current_stage = None
        config.current_file = None
        await session.commit()
        return summary
    except Exception as error:
        config.last_summary = summary
        config.last_finished_at = _now()
        config.last_error = str(error)[:1000]
        config.current_stage = "failed"
        await session.commit()
        raise


class FolderSyncScheduler:
    """Small durable scheduler; jobs and manifests, not timers, are the source of truth."""

    def __init__(self, session_factory: async_sessionmaker, job_queue: JobQueue, app_settings: Settings = settings):
        self._session_factory = session_factory
        self._job_queue = job_queue
        self._settings = app_settings
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="sag-folder-sync-scheduler")

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
                await self._dispatch_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("文件夹同步调度扫描失败")
            await asyncio.sleep(self._settings.folder_sync_scheduler_poll_seconds)

    async def _dispatch_due(self) -> None:
        if not _root(self._settings).is_dir():
            return
        now = _now()
        async with self._session_factory() as session:
            configs = list(
                (await session.scalars(select(FolderSyncConfig).where(FolderSyncConfig.enabled.is_(True)))).all()
            )
            for config in configs:
                if not _is_sync_due(config, now, self._settings):
                    continue
                try:
                    await enqueue_folder_sync(session, config.source_id, self._job_queue)
                except ValidationError as error:
                    config.last_error = error.message
                    await session.commit()


def _is_sync_due(config: FolderSyncConfig, now: datetime, app_settings: Settings) -> bool:
    """Return whether a persisted schedule has reached its next occurrence."""
    if config.mode in {"watch", "interval"}:
        due_seconds = app_settings.folder_sync_watch_poll_seconds if config.mode == "watch" else config.interval_seconds
        return not config.last_started_at or (now - config.last_started_at).total_seconds() >= due_seconds

    local_now = now.astimezone(ZoneInfo(app_settings.timezone))
    hour, minute = (int(value) for value in config.schedule_time.split(":"))
    if config.mode == "daily":
        scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        days_since_schedule = (local_now.weekday() - config.weekday) % 7
        scheduled = (local_now - timedelta(days=days_since_schedule)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
    if local_now < scheduled:
        return False
    return not config.last_started_at or config.last_started_at.astimezone(ZoneInfo(app_settings.timezone)) < scheduled
