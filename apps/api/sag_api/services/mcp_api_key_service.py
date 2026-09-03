from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.errors import NotFoundError, ValidationError
from sag_api.db.models import McpApiKey, User

KEY_PREFIX = "sag_mcp_"


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _public(row: McpApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "active": row.revoked_at is None,
    }


async def list_keys(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(McpApiKey)
            .where(McpApiKey.user_id == user.id)
            .order_by(McpApiKey.created_at.desc())
        )
    ).scalars().all()
    return [_public(row) for row in rows]


async def create_key(session: AsyncSession, user: User, name: str) -> tuple[dict[str, Any], str]:
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("API Key 名称不能为空")
    if len(clean_name) > 120:
        raise ValidationError("API Key 名称不能超过 120 个字符")
    raw_key = KEY_PREFIX + secrets.token_urlsafe(32)
    row = McpApiKey(
        user_id=user.id,
        name=clean_name,
        key_prefix=raw_key[: len(KEY_PREFIX) + 8],
        key_hash=_hash(raw_key),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _public(row), raw_key


async def revoke_key(session: AsyncSession, user: User, key_id: str) -> dict[str, Any]:
    row = await session.scalar(
        select(McpApiKey).where(McpApiKey.id == key_id, McpApiKey.user_id == user.id)
    )
    if row is None:
        raise NotFoundError("MCP API Key 不存在")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
    return _public(row)


async def authenticate_key(session: AsyncSession, raw_key: str) -> McpApiKey | None:
    if not raw_key.startswith(KEY_PREFIX):
        return None
    row = await session.scalar(
        select(McpApiKey).where(
            McpApiKey.key_hash == _hash(raw_key),
            McpApiKey.revoked_at.is_(None),
        )
    )
    if row is None:
        return None
    row.last_used_at = datetime.now(UTC)
    await session.commit()
    return row


def public_descriptor_auth() -> dict[str, str]:
    return {
        "type": "bearer",
        "header": "Authorization",
        "format": "Bearer sag_mcp_<generated-key>",
        "note": "在设置页生成 API Key；密钥只显示一次。也兼容登录态 JWT。",
    }
