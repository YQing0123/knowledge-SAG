from __future__ import annotations

from sag_api.services.deep_research import _citation_key, _sufficient


def test_deep_research_requires_coverage_and_independent_sources():
    citations = [
        {
            "source_id": "source-a",
            "chunk_id": "a-1",
            "heading": "政务数据共享机制",
            "snippet": "责任分工和数据共享流程由牵头部门负责。",
            "score": 0.72,
        },
        {
            "source_id": "source-a",
            "chunk_id": "a-2",
            "heading": "共享责任",
            "snippet": "责任分工应当明确到部门和岗位。",
            "score": 0.46,
        },
        {
            "source_id": "source-b",
            "chunk_id": "b-1",
            "heading": "数据共享实施",
            "snippet": "政务数据共享工作由主管部门统筹协调。",
            "score": 0.38,
        },
    ]

    insufficient, coverage = _sufficient(citations, "政务数据共享 责任分工", 1, 5)
    sufficient, final_coverage = _sufficient(citations, "政务数据共享 责任分工", 2, 5)

    assert insufficient is False
    assert sufficient is True
    assert coverage >= 0.35
    assert final_coverage == coverage


def test_deep_research_citation_key_deduplicates_same_chunk():
    first = {"source_id": "source-a", "chunk_id": "chunk-1", "heading": "章节"}
    repeated = {"source_id": "source-a", "chunk_id": "chunk-1", "heading": "章节"}

    assert _citation_key(first) == _citation_key(repeated)
