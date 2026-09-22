"""설계서 Graph: bounded retry → parallel four views → fan-in → verification."""

from langgraph.graph import END, START, StateGraph

from agents.domain import domain_agent
from agents.market import market_agent
from agents.report import report_agent
from agents.stakeholder import stakeholder_agent
from agents.synthesis import synthesis_agent
from agents.technical import technical_agent
from agents.trl import trl_agent
from evidence import fan_in
from nodes.evidence import (
    first_evidence_check,
    query_rewrite,
    record_first_missing,
    record_second_missing,
    route_first_evidence,
    route_retry_limit,
    route_second_evidence,
    second_evidence_check,
)
from nodes.verification import conflict_node, counter_evidence_node
from state import ResearchState
from workflow_logging import get_logger, log_node, log_router


def build_graph(run_id="standalone", services=None, checkpointer=None):
    # --show-graph에서는 API 키/모델 다운로드 없이 컴파일 가능.
    logger = get_logger(run_id)
    logger.info("GRAPH_BUILD | 설계서 기반 Graph 구성 시작")
    builder = StateGraph(ResearchState)
    agents = {
        "technical": technical_agent,
        "query_rewrite": query_rewrite,
        "trl": trl_agent,
        "market": market_agent,
        "stakeholder": stakeholder_agent,
        "domain": domain_agent,
        "counter_evidence": counter_evidence_node,
        "conflict": conflict_node,
        "synthesis": synthesis_agent,
        "report": report_agent,
    }

    def bind(function):
        def invoke(state):
            if services is None:
                raise ValueError("실행에는 Services 필요; CLI --run 또는 --demo 사용")
            return function(state, services)

        return invoke

    nodes = {name: bind(function) for name, function in agents.items()}
    nodes.update(
        {
            "first_check": first_evidence_check,
            "retry_limit": lambda state: {},
            "record_first_missing": record_first_missing,
            "fan_out": lambda state: {},
            "fan_in": fan_in,
            "second_check": second_evidence_check,
            "record_second_missing": record_second_missing,
        }
    )
    for name, function in nodes.items():
        builder.add_node(name, log_node(name, function, logger))
    builder.add_edge(START, "technical")
    builder.add_edge("technical", "first_check")
    builder.add_conditional_edges(
        "first_check",
        log_router(route_first_evidence, logger),
        {"retry": "retry_limit", "evaluate": "fan_out"},
    )
    builder.add_conditional_edges(
        "retry_limit",
        log_router(route_retry_limit, logger),
        {"rewrite": "query_rewrite", "missing": "record_first_missing"},
    )
    builder.add_edge("query_rewrite", "technical")
    builder.add_edge("record_first_missing", "fan_out")
    evaluators = ["trl", "market", "stakeholder", "domain"]
    for name in evaluators:
        builder.add_edge("fan_out", name)
    builder.add_edge(evaluators, "fan_in")
    builder.add_edge("fan_in", "second_check")
    builder.add_conditional_edges(
        "second_check",
        log_router(route_second_evidence, logger),
        {"missing": "record_second_missing", "verify": "counter_evidence"},
    )
    builder.add_edge("record_second_missing", "counter_evidence")
    builder.add_edge("counter_evidence", "conflict")
    builder.add_edge("conflict", "synthesis")
    builder.add_edge("synthesis", "report")
    builder.add_edge("report", END)
    logger.info("GRAPH_READY | nodes=%d | parallel=4", len(nodes))
    return builder.compile(checkpointer=checkpointer)
