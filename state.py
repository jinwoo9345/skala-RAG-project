"""Supervisor 패턴 State.

- 작업 페이로드: 하위 에이전트의 산출물. 각 에이전트는 자기 결과 필드만 쓴다.
- 제어 메타: Supervisor의 라우팅·종료·재개 판단에 필요한 최소치.
- 결정 로그(사유 포함)는 State에 쌓지 않고 로거·LangSmith로 보내며 trace_id로 연결한다.
- Supervisor가 한 번에 하나의 에이전트만 실행하므로 동시 쓰기가 없어 Reducer를 두지 않는다.
"""

from typing import TypedDict

from config import DOMAIN, MAX_RETRIES, MAX_STEPS, TECHNOLOGIES
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


class Directive(TypedDict):
    target: str  # 다음에 실행할 하위 에이전트 또는 "end"
    reason: str
    missing_items: list[dict]  # 재작업 시 다시 조사할 항목만 전달


class ResearchState(TypedDict):
    # ── 작업 페이로드 ──
    technologies: list[TechnologySelection]
    domain: str
    technical_evidence: dict[str, Evidence]
    trl_analysis: dict
    market_analysis: dict
    stakeholder_analysis: dict
    domain_analysis: dict
    counter_evidence: dict
    conflicts: list[dict]
    synthesis: dict
    references: list[dict]
    final_report: str
    eval_result: dict | None  # 품질 평가 verdict

    # ── 제어 메타 ──
    trace_id: str  # 외부 로그·LangSmith 트레이스와 잇는 상관 키(= run_id)
    step_count: int  # Supervisor 결정 횟수(종료 가드)
    max_steps: int
    directive: Directive | None  # 현재 지시. 이전 지시는 누적하지 않는다.
    node_status: dict[str, str]  # 에이전트별 done/failed/stale. 재개·재시도 판단용
    rework_counts: dict[str, int]  # 관점 에이전트·품질 Loop의 재작업 횟수
    last_error: str | None
    search_queries: list[str]  # 기술 조사의 현재 검색 질의(덮어쓰기)
    retry_count: int  # 기술 조사 재검색 횟수
    max_retries: int
    missing_evidence: list[dict]  # Supervisor가 매 결정마다 다시 계산해 덮어쓴다


def technology_names(state) -> list[str]:
    return [item["name"] if isinstance(item, dict) else item for item in state["technologies"]]


def initial_state(
    *, max_retries: int = MAX_RETRIES, trace_id: str = "standalone", max_steps: int = MAX_STEPS
) -> ResearchState:
    if not 0 <= max_retries <= 10:
        raise ValueError("max_retries는 0~10 사이여야 함")
    if max_steps < 1:
        raise ValueError("max_steps는 1 이상이어야 함")
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
        "eval_result": None,
        "trace_id": trace_id,
        "step_count": 0,
        "max_steps": max_steps,
        "directive": None,
        "node_status": {},
        "rework_counts": {},
        "last_error": None,
    }
