"""도메인 평가: 기술 State + RAG. 비교 문서는 보조 맥락으로 분리한다."""

from agents.common import evaluate, merge_analysis, rework_items
from config import DOMAIN_CRITERIA, SUPPORTING_TECHNOLOGIES
from state import technology_names


def domain_agent(state, services):
    # Supervisor 재작업이면 부족한 (기술, 기준)만 다시 검색하고 기존 분석에 합친다.
    wanted = rework_items(state, "domain")
    previous = state["domain_analysis"] if wanted else None
    sources = {}
    for technology in technology_names(state):
        criteria = [c for c in DOMAIN_CRITERIA if wanted is None or (technology, c) in wanted]
        chunks = {}
        lists = [
            services.retriever.search(
                f"{technology} {state['domain']} {criterion}",
                perspective="domain",
                technology=technology,
                role="core",
            )
            for criterion in criteria
        ]
        for rank in range(services.settings.top_k):
            for results in lists:
                if rank < len(results):
                    chunks.setdefault(results[rank]["chunk_id"], results[rank])
        sources[technology] = list(chunks.values())[: services.settings.max_context_chunks]
    # 보조 문서는 각 문서의 기술명으로 추출해 대상 기술의 직접 성능 근거와 분리한다.
    for technology in SUPPORTING_TECHNOLOGIES if wanted is None else ():
        supporting = services.retriever.search(
            f"{technology} KV cache architecture data movement limitations",
            perspective="domain",
            technology=technology,
            role="supporting",
        )
        if supporting:
            sources[technology] = supporting
    evidence = dict(state["technical_evidence"])
    if previous:
        evidence.update(previous["evidence"])
    result = evaluate(state, services, "domain", sources, evidence)
    return merge_analysis(previous, result, "domain")
