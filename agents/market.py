"""시장 평가: Web Search 자료만 사용한다."""

from agents.common import evaluate_perspective


def market_agent(state, services):
    return evaluate_perspective(state, services, "market")
