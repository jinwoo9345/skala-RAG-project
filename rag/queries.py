"""기술 검색 질의 계획.

수업의 Query Rewrite 패턴처럼 Graph State에 실제 검색 질의를 보존한다.
결정론적 질의는 초기값과 LLM 재작성 검증용 기준으로만 사용한다.
"""

from config import TECHNICAL_EVIDENCE_ITEMS

TECHNICAL_TERMS = {
    "작동 원리": "architecture mechanism design",
    "KV Cache 저장 위치": "KV cache placement memory tier",
    "데이터 이동 방식": "data transfer path DMA RDMA PCIe bandwidth attention location",
    "실험 환경": "evaluation experimental setup GPU model context length batch",
    "주요 성능 결과": "evaluation throughput latency TTFT TPOT speedup",
    "비교 Baseline": "baseline comparison evaluation",
    "한계점": "limitations overhead bottleneck scalability",
    "출처": "paper authors abstract",
}


def rewrite_query(technology, item, retry_count):
    terms = TECHNICAL_TERMS.get(item, item)
    if retry_count == 0:
        return f"{technology} {item} {terms}"
    if retry_count == 1:
        return f"{technology} {terms} implementation evaluation section evidence"
    return f"{technology} {terms} measured result configuration constraint discussion"


def technical_query_targets(technologies, missing_evidence, retry_count):
    """이번 기술 조사에서 검색할 ``(technology, item)`` 순서를 만든다."""
    if retry_count == 0:
        return [(technology, item) for technology in technologies for item in TECHNICAL_EVIDENCE_ITEMS]

    missing = {
        (row.get("technology"), row.get("item"))
        for row in missing_evidence
        if row.get("stage") == 1
    }
    return [
        (technology, item)
        for technology in technologies
        for item in TECHNICAL_EVIDENCE_ITEMS
        if (technology, item) in missing
    ]


def build_search_queries(technologies, missing_evidence=(), retry_count=0):
    """State 초기화 및 테스트에서 사용할 재현 가능한 검색 질의 목록."""
    return [
        rewrite_query(technology, item, retry_count)
        for technology, item in technical_query_targets(technologies, missing_evidence, retry_count)
    ]
