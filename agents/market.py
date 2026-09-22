"""시장 평가: Web Search 자료만 사용한다."""

from agents.common import evaluate, web_sources


def market_agent(state, services):
    return evaluate(
        state,
        services,
        "market",
        web_sources(state, services, "market"),
    )
