"""기술 조사 Agent: RAG로 원문 사실을 수집하고 기존 근거와 ID 기준 병합."""

from evidence import extract_evidence
from rag.queries import technical_query_targets
from state import technology_names


def technical_agent(state, services):
    evidence = dict(state["technical_evidence"])
    targets = technical_query_targets(
        technology_names(state), state["missing_evidence"], state["retry_count"]
    )
    queries = state["search_queries"]
    if len(queries) != len(targets):
        raise ValueError("State search_queries와 기술 조사 대상 개수가 일치하지 않음")
    planned = {}
    for (technology, item), query in zip(targets, queries):
        planned.setdefault(technology, []).append((item, query))

    for technology in technology_names(state):
        item_queries = planned.get(technology, [])
        if not item_queries:
            continue
        items = [item for item, _ in item_queries]
        chunks = {}
        # 순위별 round-robin으로 여러 항목의 검색 결과가 문맥 한도를 공유한다.
        lists = [
            services.retriever.search(
                query,
                perspective="technical",
                technology=technology,
                role="core",
            )
            for _, query in item_queries
        ]
        for rank in range(services.settings.top_k):
            for results in lists:
                if rank < len(results):
                    chunks.setdefault(results[rank]["chunk_id"], results[rank])
        selected = list(chunks.values())[: services.settings.max_context_chunks]
        evidence.update(
            extract_evidence(services, selected, technology=technology, perspective="technical", items=items)
        )
    return {"technical_evidence": evidence}
