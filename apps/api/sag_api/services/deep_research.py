"""Bounded multi-round research orchestration for native conversations.

The orchestrator deliberately exposes progress and evidence metadata only. It
never forwards a provider's hidden chain of thought to the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sag_api.generation import LLMClient
from sag_api.tools.base import ToolContext
from sag_api.tools.builtin import SearchContextTool, WebSearchTool


@dataclass(frozen=True, slots=True)
class ResearchUpdate:
    type: str
    payload: dict[str, Any]
    evidence: str = ""
    citations: tuple[dict[str, Any], ...] = ()
    external_references: tuple[dict[str, Any], ...] = ()


def _public_stats(value: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON-safe scalar retrieval counters for the SSE payload."""
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (str, int, float, bool)) or item is None
    }


def _citation_key(citation: dict[str, Any]) -> tuple[str, str, str]:
    """Merge repeated retrieval results before evaluating research coverage."""

    return (
        str(citation.get("source_id") or ""),
        str(citation.get("chunk_id") or ""),
        str(citation.get("heading") or "").strip().casefold(),
    )


def _evidence_coverage(citations: list[dict[str, Any]], query: str) -> float:
    """Estimate how much of the original question is represented in evidence."""

    import re

    terms = list(dict.fromkeys(re.findall(r"[\w\u4e00-\u9fff]{2,}", query.casefold())))[:24]
    if not terms:
        return 0.0
    evidence = " ".join(
        f"{item.get('heading') or ''} {item.get('snippet') or ''}" for item in citations
    ).casefold()
    return sum(term in evidence for term in terms) / len(terms)


def _sufficient(
    citations: list[dict[str, Any]], query: str, round_number: int, max_rounds: int
) -> tuple[bool, float]:
    source_ids = {str(item.get("source_id") or "") for item in citations if item.get("source_id")}
    strong = sum(1 for item in citations if float(item.get("score") or 0) >= 0.35)
    coverage = _evidence_coverage(citations, query)
    # Mirror the stronger DSH research threshold: wait for independent sources,
    # sufficiently relevant evidence, and coverage of the user's actual terms.
    sufficient = (
        round_number >= 2
        and len(source_ids) >= 2
        and strong >= 3
        and coverage >= 0.35
    ) or round_number >= max_rounds
    return sufficient, coverage


async def _rewrite_query(llm: LLMClient, original: str, current: str, round_number: int) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "你是知识库检索查询改写器。只输出一条简洁的中文检索查询，不要解释、编号或引号。"
                "保留原问题的实体和时间范围，补充本轮可能缺失的事实角度。"
            ),
        },
        {
            "role": "user",
            "content": f"原问题：{original}\n当前查询：{current}\n这是第 {round_number} 轮后的改写。",
        },
    ]
    try:
        rewritten = await asyncio.wait_for(llm.complete(prompt), timeout=20)
    except Exception:  # noqa: BLE001
        rewritten = ""
    rewritten = " ".join(str(rewritten or "").split()).strip("\"'「」")
    if not rewritten or rewritten.casefold() == current.casefold():
        rewritten = f"{original} 关键事实 证据 来源"
    return rewritten[:400]


async def iter_research(
    *,
    query: str,
    llm: LLMClient,
    context: ToolContext,
    web_enabled: bool,
    max_rounds: int = 5,
) -> AsyncIterator[ResearchUpdate]:
    """Run bounded retrieval rounds and yield auditable progress updates."""

    max_rounds = max(1, min(int(max_rounds), 5))
    current = query.strip()
    evidence_blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    external_references: list[dict[str, Any]] = []
    search_tool = SearchContextTool()

    yield ResearchUpdate("research.started", {"query": query, "max_rounds": max_rounds})
    if not context.sources:
        yield ResearchUpdate(
            "research.progress",
            {"round": 0, "evidence_hits": 0, "source_count": 0, "strong_hits": 0, "sufficient": False},
        )
        yield ResearchUpdate("research.completed", {"rounds": 0, "evidence_hits": 0, "reason": "no_sources"})
        max_rounds = 0
    for round_number in range(1, max_rounds + 1):
        yield ResearchUpdate(
            "retrieval.started",
            {"query": current, "round": round_number, "source_ids": [s.id for s in context.sources]},
        )
        context.citation_offset = len(citations)
        result = await search_tool.invoke({"query": current, "top_k": 8}, context)
        retrieved_citations = [dict(item) for item in result.citations]
        existing_keys = {_citation_key(item) for item in citations}
        new_citations = [
            item for item in retrieved_citations
            if _citation_key(item) not in existing_keys
            and not existing_keys.add(_citation_key(item))
        ]
        citations.extend(new_citations)
        if result.content and result.content != "（无相关资料）":
            evidence_blocks.append(f"第 {round_number} 轮知识库检索（查询：{current}）：\n{result.content}")
        yield ResearchUpdate(
            "retrieval.completed",
            {
                "query": current,
                "round": round_number,
                "retrieval_hits": len(new_citations),
                "total_hits": len(citations),
                "citations": new_citations,
                "stats": _public_stats(result.data),
            },
            citations=tuple(new_citations),
        )
        source_count = len({str(item.get("source_id") or "") for item in citations if item.get("source_id")})
        strong_hits = sum(1 for item in citations if float(item.get("score") or 0) >= 0.45)
        sufficient, coverage = _sufficient(citations, query, round_number, max_rounds)
        yield ResearchUpdate(
            "research.progress",
            {
                "round": round_number,
                "evidence_hits": len(citations),
                "source_count": source_count,
                "strong_hits": strong_hits,
                "coverage": round(coverage, 3),
                "sufficient": sufficient,
            },
        )
        if sufficient or round_number >= max_rounds:
            yield ResearchUpdate(
                "research.completed",
                {
                    "rounds": round_number,
                    "evidence_hits": len(citations),
                    "reason": "evidence_sufficient" if sufficient else "max_rounds",
                },
            )
            break
        next_query = await _rewrite_query(llm, query, current, round_number)
        current = next_query
        yield ResearchUpdate(
            "research.query_rewritten",
            {"round": round_number + 1, "query": current, "rationale": "补充不同角度并交叉核验证据"},
        )

    if web_enabled and WebSearchTool.configured():
        yield ResearchUpdate("web_search.started", {"query": query})
        try:
            web_result = await WebSearchTool().invoke({"query": query, "count": 6}, context)
            references = web_result.data.get("external_references") if isinstance(web_result.data, dict) else []
            if isinstance(references, list):
                external_references.extend(dict(item) for item in references if isinstance(item, dict))
            if web_result.content and web_result.content != "（联网搜索未返回可用结果）":
                evidence_blocks.append(f"联网搜索（查询：{query}）：\n{web_result.content}")
            yield ResearchUpdate(
                "web_search.completed",
                {"query": query, "web_hits": len(external_references), "references": external_references},
                external_references=tuple(external_references),
            )
        except Exception as error:  # noqa: BLE001
            yield ResearchUpdate("web_search.failed", {"query": query, "error": str(error)[:300]})

    context_text = "\n\n".join(evidence_blocks)
    if context_text:
        context_text = context_text[:24_000]
    yield ResearchUpdate(
        "research.context_ready",
        {"evidence_hits": len(citations), "web_hits": len(external_references)},
        evidence=context_text,
        citations=tuple(citations),
        external_references=tuple(external_references),
    )
