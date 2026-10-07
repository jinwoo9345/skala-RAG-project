"""Supervisor 패턴 Graph: supervisor ⇄ 하위 에이전트 (hub-and-spoke).

- 모든 하위 에이전트의 나가는 엣지는 supervisor 하나뿐이다(하위 에이전트 간 직접 통신 없음).
- supervisor의 분기는 add_conditional_edges 하나로, State에 기록된 directive를 따른다.
"""

from langgraph.graph import END, START, StateGraph

from agents.domain import domain_agent
from agents.market import market_agent
from agents.report import report_agent
from agents.stakeholder import stakeholder_agent
from agents.synthesis import synthesis_agent
from agents.technical import technical_agent
from agents.trl import trl_agent
from nodes.quality_eval import quality_eval_node
from nodes.verification import verification_node
from state import ResearchState
from supervisor import AGENTS, END_TARGET, route_next, supervisor_node
from workflow_logging import error_location, get_logger, log_node

AGENT_FUNCTIONS = {
    "technical": technical_agent,
    "trl": trl_agent,
    "market": market_agent,
    "stakeholder": stakeholder_agent,
    "domain": domain_agent,
    "verification": verification_node,
    "synthesis": synthesis_agent,
    "report": report_agent,
    "quality_eval": quality_eval_node,
}
assert tuple(AGENT_FUNCTIONS) == AGENTS


def build_graph(run_id="standalone", services=None, checkpointer=None):
    # --show-graph에서는 API 키/모델 다운로드 없이 컴파일 가능.
    logger = get_logger(run_id)
    logger.info("GRAPH_BUILD | Supervisor Graph 구성 시작")
    builder = StateGraph(ResearchState)

    def bind(name, function):
        def invoke(state):
            if services is None:
                raise ValueError("실행에는 Services 필요; CLI --run 또는 --demo 사용")
            try:
                result = function(state, services)
            except Exception as exc:  # noqa: BLE001 -- 실패를 State에 남기고 Supervisor가 재시도/한계 기록을 정한다
                logger.error("AGENT_FAILED | %s | %s | 위치=%s", name, type(exc).__name__, error_location(exc))
                return {
                    "node_status": {**state["node_status"], name: "failed"},
                    "last_error": f"{name}: {type(exc).__name__}",
                }
            return {**result, "node_status": {**state["node_status"], name: "done"}, "last_error": None}

        return invoke

    builder.add_node("supervisor", log_node("supervisor", supervisor_node, logger))
    for name, function in AGENT_FUNCTIONS.items():
        builder.add_node(name, log_node(name, bind(name, function), logger))
        builder.add_edge(name, "supervisor")
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor", route_next, {**{name: name for name in AGENTS}, END_TARGET: END}
    )
    logger.info("GRAPH_READY | supervisor + agents=%d", len(AGENTS))
    return builder.compile(checkpointer=checkpointer)
