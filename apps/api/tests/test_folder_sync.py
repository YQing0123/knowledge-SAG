from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from sag_api.core.config import Settings
from sag_api.core.errors import ValidationError
from sag_api.db.models import Document, FolderSyncConfig, FolderSyncEntry, Source
from sag_api.enums import ConnectorKind, SourceType
from sag_api.services import markdown_image_service
from sag_api.services.folder_sync_service import (
    _is_sync_due,
    _matches,
    list_sync_directories,
    resolve_sync_directory,
    run_folder_sync,
    update_folder_sync_config,
)


class RecordingQueue:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.ids.append(job_id)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/ignored.db",
        data_dir=str(tmp_path / "engine"),
        upload_dir=str(tmp_path / "uploads"),
        folder_sync_root=str(tmp_path / "sync"),
        max_upload_mb=5,
        folder_sync_max_files_per_run=20,
    )


def test_folder_sync_directory_is_confined(tmp_path: Path):
    app_settings = _settings(tmp_path)
    assert resolve_sync_directory("team/notes", app_settings) == (tmp_path / "sync" / "team" / "notes").resolve()
    with pytest.raises(ValidationError):
        resolve_sync_directory("../outside", app_settings)
    with pytest.raises(ValidationError):
        resolve_sync_directory("/etc", app_settings)


def test_folder_sync_directory_browser_is_confined_and_preserves_dot_names(tmp_path: Path):
    app_settings = _settings(tmp_path)
    (tmp_path / "sync" / ".obsidian").mkdir(parents=True)
    (tmp_path / "sync" / "notes").mkdir()

    listing = list_sync_directories("", app_settings)

    assert listing.current_path == "."
    assert [directory.path for directory in listing.directories] == [".obsidian", "notes"]
    with pytest.raises(ValidationError):
        list_sync_directories("../outside", app_settings)


def test_folder_sync_recursive_glob_also_matches_configured_folder_root():
    assert _matches("article.md", ["**/*.md"], [])
    assert _matches("nested/article.md", ["**/*.md"], [])
    assert not _matches("article.pdf", ["**/*.md"], [])


@pytest.mark.asyncio
async def test_folder_sync_rejects_image_enrichment_without_official_mineru(tmp_path: Path):
    from sag_api.core.db import SessionLocal, init_db
    from sag_api.schemas.folder_sync import FolderSyncConfigUpdate

    await init_db()
    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-image-config-source",
            name="Folder Sync Image Config",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_image_config_test",
        )
        session.add(source)
        await session.commit()
        with pytest.raises(ValidationError, match="MinerU"):
            await update_folder_sync_config(
                session,
                source.id,
                FolderSyncConfigUpdate(
                    enabled=False,
                    directory="team",
                    image_enrichment_enabled=True,
                ),
            )


def test_folder_sync_periodic_schedules_only_run_once_per_occurrence(tmp_path: Path):
    app_settings = _settings(tmp_path)
    now = datetime(2026, 8, 24, 1, 5, tzinfo=UTC)  # Monday 09:05 in Asia/Shanghai.

    daily = FolderSyncConfig(source_id="daily", enabled=True, mode="daily", schedule_time="09:00")
    assert _is_sync_due(daily, now, app_settings)
    daily.last_started_at = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    assert not _is_sync_due(daily, now, app_settings)

    weekly = FolderSyncConfig(source_id="weekly", enabled=True, mode="weekly", weekday=0, schedule_time="09:00")
    assert _is_sync_due(weekly, now, app_settings)
    weekly.last_started_at = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    assert not _is_sync_due(weekly, now, app_settings)
    weekly.last_started_at = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
    assert _is_sync_due(weekly, now, app_settings)


@pytest.mark.asyncio
async def test_folder_sync_creates_updates_and_deletes_manifest_documents(tmp_path: Path):
    from sag_api.core.db import SessionLocal, init_db

    await init_db()
    app_settings = _settings(tmp_path)
    folder = tmp_path / "sync" / "team"
    folder.mkdir(parents=True)
    note = folder / "guide.md"
    note.write_text("# Guide\n\nfirst version", encoding="utf-8")
    queue = RecordingQueue()

    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-source",
            name="Folder Sync",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_test",
        )
        session.add(source)
        await session.commit()
        config = FolderSyncConfig(
            source_id=source.id,
            enabled=True,
            directory="team",
            include_globs=["**/*.md", "*.md"],
            sync_deletions=True,
        )
        session.add(config)
        await session.commit()

        first = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert first == {"discovered": 1, "created": 1, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0}
        entries = list(
            (await session.scalars(select(FolderSyncEntry).where(FolderSyncEntry.source_id == source.id))).all()
        )
        assert len(entries) == 1
        first_document_id = entries[0].document_id
        assert len(queue.ids) == 1

        note.write_text("# Guide\n\nsecond version", encoding="utf-8")
        second = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert second["updated"] == 1
        entries = list((await session.scalars(select(FolderSyncEntry))).all())
        assert entries[0].document_id != first_document_id
        documents = list((await session.scalars(select(Document).where(Document.source_id == source.id))).all())
        assert len(documents) == 1
        assert len(queue.ids) == 2

        await session.delete(documents[0])
        await session.commit()
        restored = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert restored["updated"] == 1
        documents = list((await session.scalars(select(Document).where(Document.source_id == source.id))).all())
        assert len(documents) == 1

        note.unlink()
        third = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert third["deleted"] == 1
        assert list((await session.scalars(select(FolderSyncEntry))).all()) == []
        assert list((await session.scalars(select(Document).where(Document.source_id == source.id))).all()) == []


@pytest.mark.asyncio
async def test_folder_sync_scans_multiple_directories_and_keeps_image_roots_isolated(tmp_path: Path, monkeypatch):
    from sag_api.core.db import SessionLocal, init_db

    await init_db()
    app_settings = _settings(tmp_path).model_copy(
        update={
            "mineru_provider": "official",
            "mineru_base_url": "https://mineru.net/api/v4",
            "mineru_api_key": "test-key",
        }
    )
    for team in ("team-a", "team-b"):
        folder = tmp_path / "sync" / team
        (folder / "images").mkdir(parents=True)
        (folder / "images" / "diagram.png").write_bytes(b"fake-png")
        (folder / "guide.md").write_text(f"# {team}\n\n![diagram](images/diagram.png)\n", encoding="utf-8")

    async def fake_parse(self, path: str, **kwargs):
        return "识别出的图片文字"

    monkeypatch.setattr(markdown_image_service.OfficialMinerUClient, "parse", fake_parse)
    queue = RecordingQueue()
    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-multi-source",
            name="Folder Sync Multiple",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_multi_test",
        )
        session.add(source)
        await session.commit()
        session.add(
            FolderSyncConfig(
                source_id=source.id,
                enabled=True,
                directory="team-a",
                directories=["team-a", "team-b"],
                image_enrichment_enabled=True,
            )
        )
        await session.commit()

        summary = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert summary["discovered"] == 2
        assert summary["images"] == 2
        entries = list((await session.scalars(select(FolderSyncEntry))).all())
        assert {entry.relative_path for entry in entries} == {"team-a/guide.md", "team-b/guide.md"}
        assert {entry.image_enrichment_status for entry in entries} == {"ready"}


@pytest.mark.asyncio
async def test_folder_sync_adopts_matching_manual_document_without_duplicate(tmp_path: Path):
    from sag_api.core.db import SessionLocal, init_db
    from sag_api.services.document_service import create_document_from_upload

    await init_db()
    app_settings = _settings(tmp_path)
    folder = tmp_path / "sync" / "team"
    folder.mkdir(parents=True)
    note = folder / "guide.md"
    raw = b"# Guide\n"
    note.write_bytes(raw)
    queue = RecordingQueue()
    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-adopt-source",
            name="Folder Sync Adopt",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_adopt_test",
        )
        session.add(source)
        await session.commit()
        existing, _ = await create_document_from_upload(
            session, source, filename="guide.md", content_type="text/markdown", data=raw,
            upload_dir=app_settings.upload_dir, job_queue=queue,
        )
        session.add(FolderSyncConfig(source_id=source.id, enabled=True, directory="team"))
        await session.commit()

        summary = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert summary["adopted"] == 1
        assert summary["created"] == 0
        entries = list(
            (await session.scalars(select(FolderSyncEntry).where(FolderSyncEntry.source_id == source.id))).all()
        )
        assert len(entries) == 1 and entries[0].document_id == existing.id
        assert entries[0].raw_content_hash == hashlib.sha256(raw).hexdigest()
        documents = list((await session.scalars(select(Document).where(Document.source_id == source.id))).all())
        assert len(documents) == 1


@pytest.mark.asyncio
async def test_folder_sync_enriches_local_markdown_images_with_traceable_callout(tmp_path: Path, monkeypatch):
    from sag_api.core.db import SessionLocal, init_db

    await init_db()
    app_settings = _settings(tmp_path).model_copy(
        update={
            "mineru_provider": "official",
            "mineru_base_url": "https://mineru.net/api/v4",
            "mineru_api_key": "test-key",
        }
    )
    folder = tmp_path / "sync" / "team"
    (folder / "images").mkdir(parents=True)
    image = folder / "images" / "diagram.png"
    image.write_bytes(b"fake-png")
    note = folder / "guide.md"
    note.write_text("# Guide\n\n![diagram](images/diagram.png)\n", encoding="utf-8")

    async def fake_parse(self, path: str, **kwargs):
        return "识别出的图片文字"

    monkeypatch.setattr(markdown_image_service.OfficialMinerUClient, "parse", fake_parse)
    queue = RecordingQueue()
    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-image-source",
            name="Folder Sync Images",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_image_test",
        )
        session.add(source)
        await session.commit()
        session.add(
            FolderSyncConfig(
                source_id=source.id,
                enabled=True,
                directory="team",
                image_enrichment_enabled=True,
            )
        )
        await session.commit()

        summary = await run_folder_sync(session, source, queue, app_settings=app_settings)
        assert summary["images"] == 1
        entry = await session.scalar(select(FolderSyncEntry).where(FolderSyncEntry.source_id == source.id))
        assert entry is not None and entry.image_enrichment_status == "ready"
        document = await session.get(Document, entry.document_id)
        assert document is not None
        stored = Path(document.storage_path).read_text(encoding="utf-8")
        assert "## 图片解析补充（MinerU）" in stored
        assert "> - Markdown 行号：3" in stored
        assert "识别出的图片文字" in stored
        assert "```text" in stored


@pytest.mark.asyncio
async def test_folder_sync_does_not_reupload_unchanged_files_when_image_collection_changes(tmp_path: Path, monkeypatch):
    from sag_api.core.db import SessionLocal, init_db

    await init_db()
    app_settings = _settings(tmp_path).model_copy(
        update={
            "mineru_provider": "official",
            "mineru_base_url": "https://mineru.net/api/v4",
            "mineru_api_key": "test-key",
        }
    )
    folder = tmp_path / "sync" / "team"
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "diagram.png").write_bytes(b"fake-png")
    (folder / "guide.md").write_text("# Guide\n\n![diagram](images/diagram.png)\n", encoding="utf-8")

    async def fake_parse(self, path: str, **kwargs):
        return "识别出的图片文字"

    monkeypatch.setattr(markdown_image_service.OfficialMinerUClient, "parse", fake_parse)
    queue = RecordingQueue()
    async with SessionLocal() as session:
        source = Source(
            id="folder-sync-image-toggle-source",
            name="Folder Sync Image Toggle",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_folder_sync_image_toggle_test",
        )
        session.add(source)
        await session.commit()
        config = FolderSyncConfig(source_id=source.id, enabled=True, directory="team")
        session.add(config)
        await session.commit()

        first = await run_folder_sync(session, source, queue, app_settings=app_settings)
        config.image_enrichment_enabled = True
        await session.commit()
        second = await run_folder_sync(session, source, queue, app_settings=app_settings)

        assert first["created"] == 1
        assert second["unchanged"] == 1
        assert second["created"] == 0 and second["updated"] == 0
        documents = list((await session.scalars(select(Document).where(Document.source_id == source.id))).all())
        assert len(documents) == 1


@pytest.mark.asyncio
async def test_markdown_image_paths_outside_root_are_rejected(tmp_path: Path):
    from sag_api.services.markdown_image_service import collect_image_references

    root = tmp_path / "sync"
    folder = root / "team"
    folder.mkdir(parents=True)
    refs = collect_image_references("![escape](../../outside.png)", folder / "note.md", root)
    assert refs[0].error == "图片路径超出同步目录"


def test_markdown_image_paths_can_be_root_relative(tmp_path: Path):
    from sag_api.services.markdown_image_service import collect_image_references

    root = tmp_path / "sync"
    folder = root / "team"
    image = folder / "assets" / "diagram.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"fake-png")
    note = folder / "note.md"

    refs = collect_image_references("![diagram](team/assets/diagram.png)", note, root)

    assert refs[0].path == image
    assert refs[0].error is None


def test_mineru_image_result_requires_text_not_only_an_asset_link():
    from sag_api.services.markdown_image_service import _has_textual_content

    assert not _has_textual_content("![](images/extracted.png)")
    assert _has_textual_content("# 招股意向书\n\n宇树科技")
