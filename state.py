"""설계서의 16개 State 필드. 병렬 노드는 자신의 analysis만 쓴다.

references는 report에서 실제 인용 출처만 병합하여 확정한다.
missing_evidence는 근거 검사/기록 노드만 변경한다.
재검색 질의는 search_queries에 보존하여 실행 결과에서 그대로 추적할 수 있다.
"""

from typing import TypedDict

from config import DOMAIN, MAX_RETRIES, TECHNOLOGIES
from rag.queries import build_search_queries


class Evidence(TypedDict):
    evidence_id: str
    technology: str
    perspective: str
    claim: str
    source: str
    page: int | None
    experimental_condition: str
    condition_source: dict
    quote: str
    source_url: str
    document_id: str
    chunk_id: str
    role: str
    item: str
    kind: str
    numeric: bool
    speaker: str
    affiliation: str


class TechnologySelection(TypedDict):
    name: str
    category: str
    key_approach: str
    selection_reason: str


class ResearchState(TypedDict):
    technologies: list[TechnologySelection]
    domain: str
    search_queries: list[str]
    technical_evidence: dict[str, Evidence]
    trl_analysis: dict
    market_analysis: dict
    stakeholder_analysis: dict
    domain_analysis: dict
    missing_evidence: list[dict]
    retry_count: int
    max_retries: int
    counter_evidence: dict
    conflicts: list[dict]
    synthesis: dict
    references: list[dict]
    final_report: str


def technology_names(state) -> list[str]:
    return [item["name"] if isinstance(item, dict) else item for item in state["technologies"]]


def initial_state(*, max_retries: int = MAX_RETRIES) -> ResearchState:
    if not 0 <= max_retries <= 10:
        raise ValueError("max_retries는 0~10 사이여야 함")
    return {
        "technologies": [
            {
                "name": "ITME",
                "category": "HW",
                "key_approach": "CXL/NVMe 기반 계층형 메모리 확장",
                "selection_reason": (
                    "KV Cache 저장 공간을 계층적으로 확장하여 HBM 용량 한계를 줄이는 접근을 평가하기 위해 선정"
                ),
            },
            {
                "name": "CXL-PIM",
                "category": "HW",
                "key_approach": "CXL 확장과 메모리 근접 연산",
                "selection_reason": (
                    "메모리 확장과 함께 Attention 관련 데이터 이동을 줄이는 접근을 평가하기 위해 선정"
                ),
            },
        ],
        "domain": DOMAIN,
        "search_queries": build_search_queries(TECHNOLOGIES),
        "technical_evidence": {},
        "trl_analysis": {},
        "market_analysis": {},
        "stakeholder_analysis": {},
        "domain_analysis": {},
        "missing_evidence": [],
        "retry_count": 0,
        "max_retries": max_retries,
        "counter_evidence": {},
        "conflicts": [],
        "synthesis": {},
        "references": [],
        "final_report": "",
    }
