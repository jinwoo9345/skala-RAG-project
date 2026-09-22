"""이해관계자 평가: Web Search, Fact/Opinion/Inference 분리."""

from agents.common import evaluate, web_sources


def stakeholder_agent(state, services):
    return evaluate(
        state,
        services,
        "stakeholder",
        web_sources(state, services, "stakeholder"),
    )
