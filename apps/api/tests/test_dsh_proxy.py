from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


def test_dsh_markdown_exports_are_confined_to_configured_root(monkeypatch, tmp_path):
    from sag_api.api.v1.dsh import _write_markdown
    from sag_api.core.config import settings

    monkeypatch.setattr(settings, "conversation_export_root", str(tmp_path))
    monkeypatch.setattr(settings, "conversation_archive_dir", "知识层/会话归档")
    written = Path(_write_markdown("archive", "验收：/ 不合法文件名", "# 可读内容"))

    assert written.is_relative_to(tmp_path)
    assert written.read_text(encoding="utf-8") == "# 可读内容\n"


def test_dsh_internal_citation_export_prefers_document_title():
    from sag_api.api.v1.dsh import _citation_label

    assert (
        _citation_label(
            {
                "document_name": "可信数据空间建设指南.md",
                "source_name": "外部素材收集",
                "heading": "不应作为文章标题",
            }
        )
        == "可信数据空间建设指南 - 外部素材收集"
    )


@pytest.mark.asyncio
async def test_dsh_proxy_requires_sag_auth():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/dsh/capabilities")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dsh_session_delete_removes_its_messages():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": "dsh-delete@t.com", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            created = await client.post("/api/v1/dsh/sessions", headers=headers, json={})
            assert created.status_code == 201
            session_id = created.json()["id"]
            message = await client.post(
                f"/api/v1/dsh/sessions/{session_id}/messages",
                headers=headers,
                json={"role": "user", "content": "临时草稿"},
            )
            assert message.status_code == 201

            deleted = await client.delete(f"/api/v1/dsh/sessions/{session_id}", headers=headers)
            assert deleted.status_code == 200
            assert deleted.json() == {"ok": True}
            sessions = await client.get("/api/v1/dsh/sessions", headers=headers)
            assert session_id not in {item["id"] for item in sessions.json()}
            messages = await client.get(f"/api/v1/dsh/sessions/{session_id}/messages", headers=headers)
            assert messages.status_code == 404


@pytest.mark.asyncio
async def test_dsh_direct_at_mention_is_persisted_as_source_scope():
    from sag_api.api.v1.dsh import _resolve_dsh_source_scope
    from sag_api.core.db import SessionLocal, init_db
    from sag_api.db.models import Source
    from sag_api.enums import ConnectorKind, SourceType

    await init_db()
    async with SessionLocal() as session:
        source = Source(
            id="dsh-mentioned-source",
            name="互联互通对接",
            source_type=SourceType.DOCUMENT,
            connector_kind=ConnectorKind.FILE_UPLOAD,
            sag_source_config_id="src_dsh_mention_test",
        )
        session.add(source)
        await session.commit()
        source_ids, query, names = await _resolve_dsh_source_scope(
            session,
            [],
            "@互联互通对接 区域功能节点中数据产品登记接口调用参数",
        )

    assert source_ids == ["dsh-mentioned-source"]
    assert query == "区域功能节点中数据产品登记接口调用参数"
    assert names == ["互联互通对接"]


@pytest.mark.asyncio
async def test_dsh_proxy_reports_missing_server_gateway_config(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.main import app

    monkeypatch.setattr(settings, "dsh_gateway_base", None)
    monkeypatch.setattr(settings, "dsh_gateway_token", None)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": "dsh-proxy@t.com", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            response = await client.get("/api/v1/dsh/capabilities", headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "configuration_error"


@pytest.mark.asyncio
async def test_dsh_proxy_forwards_authorized_sse_without_leaking_token(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.main import app

    monkeypatch.setattr(settings, "dsh_gateway_base", "http://gateway/ext/sag-agent-gateway")
    monkeypatch.setattr(settings, "dsh_gateway_token", "server-only-token")
    received: dict[str, object] = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        received["authorization"] = request.headers.get("authorization")
        received["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'event: run.started\ndata: {"type":"run.started"}\n\n'
                b'event: run.completed\ndata: {"type":"run.completed","content":"ok"}\n\n'
            ),
        )

    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake_handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with original_client(transport=transport, base_url="http://t") as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": "dsh-stream@t.com", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            response = await client.post(
                "/api/v1/dsh/stream",
                headers=headers,
                json={
                    "query": "test",
                    "provider": "p",
                    "model": "m",
                    "mode": "controlled",
                    "source_ids": ["source-a"],
                    "evidence": [{"content": "evidence"}],
                },
            )
    assert response.status_code == 200
    assert "run.completed" in response.text
    assert received["authorization"] == "Bearer server-only-token"
    payload = received["payload"]
    assert isinstance(payload, dict)
    assert payload["query"] == "test"
    assert payload["provider"] == "p"
    assert payload["model"] == "m"
    assert payload["source_ids"] == ["source-a"]
    assert payload["permission_preset"] == "workspace-write"
    assert payload["web_enabled"] is False
    assert payload["history"] == []
    assert payload["images"] == []
    assert isinstance(payload["delegation_token"], str) and payload["delegation_token"]
    assert "server-only-token" not in payload["delegation_token"]


@pytest.mark.asyncio
async def test_dsh_proxy_preserves_research_and_degradation_events(monkeypatch):
    from sag_api.api.v1.dsh import _proxy_stream
    from sag_api.core.config import settings

    monkeypatch.setattr(settings, "dsh_gateway_timeout_seconds", 5)
    body = (
        b'event: research.started\ndata: {"type":"research.started","max_rounds":5}\n\n'
        b'event: retrieval.started\ndata: {"type":"retrieval.started","round":1}\n\n'
        b'event: retrieval.completed\ndata: {"type":"retrieval.completed","round":2}\n\n'
        b'event: research.progress\ndata: {"type":"research.progress","coverage":0.5}\n\n'
        b'event: research.query_rewritten\ndata: {"type":"research.query_rewritten","query":"x context"}\n\n'
        b'event: web_search.failed\ndata: {"type":"web_search.failed","error":"timeout"}\n\n'
        b'event: run.degraded\ndata: {"type":"run.degraded","to":"controlled_rag"}\n\n'
        b'event: run.completed\ndata: {"type":"run.completed","route":{"mode":"agent-timeout-fallback"}}\n\n'
    )

    async def fake_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake_handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    events = [event async for event in _proxy_stream("http://gateway/ext/sag-agent-gateway/agent", {"query": "x"})]
    assert [event["event"] for event in events] == [
        "research.started",
        "retrieval.started",
        "retrieval.completed",
        "research.progress",
        "research.query_rewritten",
        "web_search.failed",
        "run.degraded",
        "run.completed",
    ]
    assert json.loads(events[-2]["data"])["to"] == "controlled_rag"
    assert json.loads(events[-1]["data"])["route"]["mode"] == "agent-timeout-fallback"
