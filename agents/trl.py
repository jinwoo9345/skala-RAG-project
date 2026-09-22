"""TRL 평가: 기술 State + Web Search. Vector DB 직접 호출 없음."""

from agents.common import evaluate, web_sources


def trl_agent(state, services):
    sources = web_sources(state, services, "trl")
    return evaluate(state, services, "trl", sources, state["technical_evidence"])
