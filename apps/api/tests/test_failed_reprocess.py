import pytest


@pytest.mark.asyncio
async def test_reprocess_failed_documents_only_queues_failed_rows():
    from sag_api.core.db import SessionLocal, init_db
    from sag_api.db.models import Document, Job, Source
    from sag_api.enums import DocumentStatus, JobStatus, JobType
    from sag_api.services.document_service import reprocess_failed_documents

    await init_db()

    class Queue:
        def __init__(self):
            self.enqueued = []

        async def enqueue_durably(self, job_id):
            self.enqueued.append(job_id)

    queue = Queue()
    async with SessionLocal() as session:
        source = Source(name="failed-bulk-reprocess", sag_source_config_id="failed-bulk-reprocess")
        session.add(source)
        await session.flush()
        failed = Document(
            source_id=source.id,
            filename="failed.md",
            content_type="text/markdown",
            size_bytes=1,
            storage_path="/tmp/failed.md",
            status=DocumentStatus.FAILED,
            error="chunk retry exhausted",
        )
        ready = Document(
            source_id=source.id,
            filename="ready.md",
            content_type="text/markdown",
            size_bytes=1,
            storage_path="/tmp/ready.md",
            status=DocumentStatus.READY,
            progress=100,
            chunk_count=1,
            event_count=1,
        )
        session.add_all([failed, ready])
        await session.flush()
        failed_job = Job(
            type=JobType.PROCESS_DOCUMENT,
            source_id=source.id,
            document_id=failed.id,
            status=JobStatus.FAILED,
            error="chunk retry exhausted",
        )
        ready_job = Job(
            type=JobType.PROCESS_DOCUMENT,
            source_id=source.id,
            document_id=ready.id,
            status=JobStatus.SUCCEEDED,
        )
        session.add_all([failed_job, ready_job])
        await session.commit()
        failed_id, ready_id = failed.id, ready.id

        jobs, skipped, requested = await reprocess_failed_documents(
            session,
            source,
            job_queue=queue,
        )

        assert requested == 1
        assert skipped == 0
        assert len(jobs) == 1
        assert queue.enqueued == [jobs[0].id]

        await session.refresh(failed)
        await session.refresh(ready)
        assert failed.status == DocumentStatus.PENDING
        assert failed.error is None
        assert ready.status == DocumentStatus.READY
        assert ready.id == ready_id
        assert failed.id == failed_id
