"""이해관계자 평가: Web Search, Fact/Opinion/Inference 분리."""

from agents.common import evaluate_perspective


def stakeholder_agent(state, services):
    return evaluate_perspective(state, services, "stakeholder")
