"""두 단계 충분성 검사와 한도 분기. 검색 실패와 근거 부족을 혼동하지 않는다."""

from config import EVALUATION_CRITERIA, PERSPECTIVES, TECHNICAL_EVIDENCE_ITEMS
from evidence import all_evidence, valid_evidence, valid_ids
from rag.queries import rewrite_query, technical_query_targets
from state import technology_names
from workflow_logging import get_logger


def first_evidence_check(state):
    evidence = [e for e in state["technical_evidence"].values() if valid_evidence(e)]
    missing = []
    for technology in technology_names(state):
        own = [e for e in evidence if e["technology"] == technology and e.get("role") == "core"]
        for item in TECHNICAL_EVIDENCE_ITEMS:
            present = bool(own) if item == "출처" else any(e["item"] == item for e in own)
            if not present:
                missing.append(
                    {
                        "stage": 1,
                        "technology": technology,
                        "item": item,
                        "reason": "출처와 원문 인용이 검증된 근거 부족",
                        "status": "pending",
                    }
                )
    get_logger().info("EVIDENCE_CHECK | stage=1 | missing=%d", len(missing))
    return {"missing_evidence": missing}


def route_first_evidence(state):
    return "retry" if any(x["stage"] == 1 for x in state["missing_evidence"]) else "evaluate"


def route_retry_limit(state):
    return "rewrite" if state["retry_count"] < state["max_retries"] else "missing"


def query_rewrite(state, services):
    """부족 항목의 질의를 재작성하고 실제 재검색 질의를 State에 저장한다."""
    count = state["retry_count"] + 1
    targets = technical_query_targets(technology_names(state), state["missing_evidence"], count)
    if services.question_rewriter is None:
        raise ValueError("question_rewriter 서비스 필요")
    inputs = [
        {"question": rewrite_query(technology, item, count)} for technology, item in targets
    ]
    queries = [query.strip() for query in services.question_rewriter.batch(inputs)]
    if len(queries) != len(targets) or any(not query for query in queries):
        raise ValueError("Query Rewrite 결과가 부족 항목과 일치하지 않음")
    get_logger().info("QUERY_REWRITE | strategy=%d | queries=%d", count, len(queries))
    return {"search_queries": queries, "retry_count": count}


def record_first_missing(state):
    get_logger().warning(
        "RETRY_EXHAUSTED | retries=%d | missing=%d", state["retry_count"], len(state["missing_evidence"])
    )
    return {"missing_evidence": [{**x, "status": "retry_exhausted"} for x in state["missing_evidence"]]}


def second_evidence_check(state):
    evidence = all_evidence(state, include_counter=False)
    missing = [dict(x) for x in state["missing_evidence"] if x["stage"] == 1]
    for perspective in PERSPECTIVES:
        findings = state[f"{perspective}_analysis"].get("findings", [])
        for technology in technology_names(state):
            for criterion in EVALUATION_CRITERIA[perspective]:
                covered = any(
                    f["technology"] == technology
                    and f["criterion"] == criterion
                    and valid_ids(f["evidence_ids"], evidence)
                    for f in findings
                )
                if not covered:
                    missing.append(
                        {
                            "stage": 2,
                            "technology": technology,
                            "perspective": perspective,
                            "item": criterion,
                            "reason": "평가 주장 또는 연결 근거 부족",
                            "status": "pending",
                        }
                    )
    get_logger().info("EVIDENCE_CHECK | stage=2 | missing=%d", sum(x["stage"] == 2 for x in missing))
    return {"missing_evidence": missing}


def route_second_evidence(state):
    return "missing" if any(x["stage"] == 2 for x in state["missing_evidence"]) else "verify"


def record_second_missing(state):
    return {
        "missing_evidence": [
            {**x, "status": "recorded"} if x["stage"] == 2 else dict(x) for x in state["missing_evidence"]
        ]
    }
