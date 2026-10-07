"""TRL 평가: 기술 State + Web Search. Vector DB 직접 호출 없음."""

from agents.common import evaluate_perspective


def trl_agent(state, services):
    return evaluate_perspective(state, services, "trl", state["technical_evidence"])
