"""Supervisor: State를 읽어 다음 하위 에이전트를 정하는 조정 계층.

- 하위 에이전트는 Supervisor와만 통신한다. 지시는 directive로 내려가고 결과는 State로 돌아온다.
- 다음 노드는 고정 순서가 아니라 State(수집된 관점, 근거 충분도, 실패, 품질 평가)로 결정한다.
- 근거 충분성 검사를 통과하거나 재작업 상한에 닿은 뒤에만 보고서 작성으로 넘어간다.
- 결정과 사유는 State에 쌓지 않고 SUPERVISOR_DECISION 로그(및 LangSmith 트레이스)로 남긴다.
"""

from config import MAX_QUALITY_LOOPS, MAX_REWORK, PERSPECTIVES
from nodes.evidence import first_evidence_check, second_evidence_check
from workflow_logging import get_logger

AGENTS = (
    "technical",
    *PERSPECTIVES,
    "verification",
    "synthesis",
    "report",
    "quality_eval",
)
END_TARGET = "end"
# 관점 재조사 후에는 그 결과를 쓰는 후속 단계를 다시 실행해야 한다.
DOWNSTREAM = {
    "perspective": ("verification", "synthesis", "report", "quality_eval"),
    "report": ("report", "quality_eval"),
}


def _missing_evidence(state):
    """현재 State 기준 부족 근거. 실행을 마친 관점만 2차 검사 대상으로 삼는다."""
    first = first_evidence_check(state)["missing_evidence"]
    exhausted = state["retry_count"] >= state["max_retries"]
    rows = [{**x, "status": "retry_exhausted" if exhausted else "pending"} for x in first]
    done = {p for p in PERSPECTIVES if state["node_status"].get(p) == "done"}
    second = second_evidence_check({**state, "missing_evidence": rows})["missing_evidence"]
    for row in second:
        if row["stage"] == 2 and row["perspective"] in done:
            reworked = state["rework_counts"].get(row["perspective"], 0) >= MAX_REWORK
            rows.append({**row, "status": "recorded" if reworked else "pending"})
    return rows


def _needs_run(state, name):
    return state["node_status"].get(name) in (None, "stale")


def _can_retry_failure(state, name):
    return state["node_status"].get(name) == "failed" and state["rework_counts"].get(name, 0) < MAX_REWORK


def decide(state):
    """(target, reason, missing_items, 추가 업데이트)를 돌려준다. 위에서부터 처음 맞는 규칙을 쓴다."""
    status, counts = state["node_status"], dict(state["rework_counts"])
    missing = _missing_evidence(state)
    updates = {"missing_evidence": missing}

    def rework(name, reason, items=(), stale=()):
        counts[name] = counts.get(name, 0) + 1
        node_status = {**status, **{x: "stale" for x in stale}}
        return name, reason, list(items), {**updates, "rework_counts": counts, "node_status": node_status}

    # 1. 종료 가드: 결정 횟수 상한에 닿으면 보고서만 만들고 끝낸다.
    if state["step_count"] >= state["max_steps"]:
        if not state["final_report"]:
            return "report", "step_limit: 상한 도달, 현재 근거로 보고서 작성", [], updates
        return END_TARGET, "step_limit: 상한 도달로 종료", [], updates

    # 2. 기술 근거: 미수집이면 수집, 부족하고 재검색이 남았으면 부족 항목만 재작업.
    if _needs_run(state, "technical"):
        return "technical", "기술 근거 미수집", [], updates
    if _can_retry_failure(state, "technical"):
        return rework("technical", "기술 조사 실패 재시도")
    first_gaps = [x for x in missing if x["stage"] == 1]
    if first_gaps and state["retry_count"] < state["max_retries"]:
        items = [{"technology": x["technology"], "item": x["item"]} for x in first_gaps]
        return "technical", f"기술 근거 부족 {len(items)}건 재검색", items, updates

    # 3. 아직 수집되지 않은 관점.
    for perspective in PERSPECTIVES:
        if _needs_run(state, perspective):
            return perspective, "관점 미수집", [], updates
        if _can_retry_failure(state, perspective):
            return rework(perspective, "관점 에이전트 실패 재시도")

    # 4. 근거가 부족한 관점은 부족 기준만 재작업을 요청한다.
    for perspective in PERSPECTIVES:
        gaps = [x for x in missing if x["stage"] == 2 and x["perspective"] == perspective]
        if gaps and counts.get(perspective, 0) < MAX_REWORK and status.get(perspective) == "done":
            items = [{"technology": x["technology"], "criterion": x["item"]} for x in gaps]
            return rework(perspective, f"{perspective} 근거 부족 {len(items)}건 재조사", items)

    # 5~8. 반대 근거 → 종합 → 보고서 → 품질 평가. 위 조건을 모두 통과해야 보고서에 도달한다.
    for name, reason in (
        ("verification", "반대 근거·Conflict 미검증"),
        ("synthesis", "관점 종합 없음"),
        ("report", "근거 충분성 확인 완료, 보고서 작성"),
        ("quality_eval", "보고서 품질 평가 필요"),
    ):
        if _needs_run(state, name):
            return name, reason, [], updates
        if _can_retry_failure(state, name):
            return rework(name, f"{name} 실패 재시도")

    # 9. 품질 미달이면 실패 항목에 맞춰 재작업한다.
    verdict = state["eval_result"]
    if verdict and not verdict["passed"] and counts.get("quality", 0) < MAX_QUALITY_LOOPS:
        failed = {name: item for name, item in verdict["items"].items() if not item["passed"]}
        items = [{"criterion": name, "reason": item["reason"]} for name, item in failed.items()]
        empty = [p for p in PERSPECTIVES if not state[f"{p}_analysis"].get("findings")]
        counts["quality"] = counts.get("quality", 0) + 1
        if "coverage" in failed and empty:
            # 비어 있는 관점을 처음부터 다시 조사하고, 그 결과를 쓰는 후속 단계도 다시 실행한다.
            target, stale, items = empty[0], (empty[0], *DOWNSTREAM["perspective"]), []
        else:
            target, stale = "report", DOWNSTREAM["report"]
        node_status = {**status, **{x: "stale" for x in stale}}
        reason = "품질 미달 재작업: " + ", ".join(failed)
        return target, reason, items, {**updates, "rework_counts": counts, "node_status": node_status}

    # 10. 종료.
    passed = verdict["passed"] if verdict else False
    return END_TARGET, "품질 평가 통과" if passed else "재작업 상한 도달로 종료", [], updates


def supervisor_node(state):
    target, reason, items, updates = decide(state)
    step = state["step_count"] + 1
    get_logger().info(
        "SUPERVISOR_DECISION | trace=%s | step=%d | next=%s | reason=%s | items=%d",
        state["trace_id"],
        step,
        target,
        reason,
        len(items),
    )
    return {**updates, "step_count": step, "directive": {"target": target, "reason": reason, "missing_items": items}}


def route_next(state):
    """add_conditional_edges용 라우터. Supervisor 노드가 정한 대상을 그대로 따른다."""
    return state["directive"]["target"]
