from __future__ import annotations

from uuid import uuid4
from pathlib import Path

import httpx
import pytest


def test_quick_capture_writes_a_frontmatter_markdown_file(tmp_path: Path):
    from sag_api.project_management.service import save_quick_capture

    filename, created_at = save_quick_capture("记录一个可复用的产品想法", root=tmp_path)

    assert filename.endswith("-快速捕捉.md")
    assert created_at.tzinfo is not None
    content = (tmp_path / filename).read_text(encoding="utf-8")
    assert "title: 快速捕捉" in content
    assert "source: SAG 个人工作台" in content
    assert "记录一个可复用的产品想法" in content


@pytest.mark.asyncio
async def test_project_archive_hides_tasks_and_blocks_new_tasks():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            suffix = uuid4().hex
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": f"pm-archive-{suffix}@test.local", "password": "password123"},
            )
            assert registered.status_code == 201
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            project = await client.post(
                "/api/v1/project-management/projects",
                headers=headers,
                json={"name": f"归档边界-{suffix}"},
            )
            assert project.status_code == 201
            project_id = project.json()["id"]
            task = await client.post(
                "/api/v1/project-management/tasks",
                headers=headers,
                json={"project_id": project_id, "title": "归档前任务"},
            )
            assert task.status_code == 201

            archived = await client.delete(
                f"/api/v1/project-management/projects/{project_id}",
                headers=headers,
            )
            assert archived.status_code == 200

            snapshot = await client.get("/api/v1/project-management/snapshot", headers=headers)
            assert snapshot.status_code == 200
            assert project_id not in {item["id"] for item in snapshot.json()["projects"]}
            assert task.json()["id"] not in {item["id"] for item in snapshot.json()["tasks"]}

            blocked = await client.post(
                "/api/v1/project-management/tasks",
                headers=headers,
                json={"project_id": project_id, "title": "不应创建"},
            )
            assert blocked.status_code == 404


@pytest.mark.asyncio
async def test_task_reorder_is_scoped_to_project_and_rejects_empty_project_id():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            suffix = uuid4().hex
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": f"pm-reorder-{suffix}@test.local", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            first = await client.post(
                "/api/v1/project-management/projects",
                headers=headers,
                json={"name": f"排序一-{suffix}"},
            )
            second = await client.post(
                "/api/v1/project-management/projects",
                headers=headers,
                json={"name": f"排序二-{suffix}"},
            )
            first_id = first.json()["id"]
            second_id = second.json()["id"]
            task_a = await client.post(
                "/api/v1/project-management/tasks",
                headers=headers,
                json={"project_id": first_id, "title": "项目一任务"},
            )
            task_b = await client.post(
                "/api/v1/project-management/tasks",
                headers=headers,
                json={"project_id": second_id, "title": "项目二任务"},
            )

            cross_project = await client.post(
                "/api/v1/project-management/tasks/reorder",
                headers=headers,
                json={"project_id": second_id, "task_ids": [task_b.json()["id"], task_a.json()["id"]]},
            )
            assert cross_project.status_code == 422

            empty_project = await client.patch(
                f"/api/v1/project-management/tasks/{task_a.json()['id']}",
                headers=headers,
                json={"project_id": "", "version": task_a.json()["version"]},
            )
            assert empty_project.status_code == 422

            valid = await client.post(
                "/api/v1/project-management/tasks/reorder",
                headers=headers,
                json={"project_id": second_id, "task_ids": [task_b.json()["id"]]},
            )
            assert valid.status_code == 200
