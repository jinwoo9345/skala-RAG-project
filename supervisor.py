"""Supervisor: State를 읽어 다음 하위 에이전트를 정하는 조정 계층.

- 하위 에이전트는 Supervisor와만 통신한다. 지시는 directive로 내려가고 결과는 State로 돌아온다.
- 다음 노드는 고정 순서가 아니라 State(수집된 관점, 근거 충분도, 실패, 품질 평가)로 결정한다.
- 수집·재작업할 관점이 여럿이면 State 요약을 보고 LLM이 다음 관점을 고른다(후보 밖 선택은 무시).
- 근거 충분성 검사를 통과하거나 재작업 상한에 닿은 뒤에만 보고서 작성으로 넘어간다.
- 스텝 상한이 가까워지면 조사를 멈추고 검증→종합→보고서→품질 평가를 마친 뒤 종료한다.
- 결정과 사유는 State에 쌓지 않고 SUPERVISOR_DECISION 로그(및 LangSmith 트레이스)로 남긴다.
"""

from config import EVALUATION_CRITERIA, MAX_QUALITY_LOOPS, MAX_REWORK, PERSPECTIVES
from nodes.evidence import first_evidence_check, second_evidence_check
from schemas import RouteChoice
from workflow_logging import error_location, get_logger

AGENTS = (
    "technical",
    *PERSPECTIVES,
    "verification",
    "synthesis",
    "report",
    "quality_eval",
)
END_TARGET = "end"
FINAL_STAGES = ("verification", "synthesis", "report", "quality_eval")
# 관점 재조사 후에는 그 결과를 쓰는 후속 단계를 다시 실행해야 한다.
DOWNSTREAM = {
    "perspective": ("verification", "synthesis", "report", "quality_eval"),
    "report": ("report", "quality_eval"),
}


def _missing_evidence(state):
    """현재 State 기준 부족 근거. 실행을 마친 관점만 2차 검사 대상으로 삼고, 미실행 관점의 공백은 따로 돌려준다."""
    first = first_evidence_check(state)["missing_evidence"]
    exhausted = state["retry_count"] >= state["max_retries"]
    rows = [{**x, "status": "retry_exhausted" if exhausted else "pending"} for x in first]
    done = {p for p in PERSPECTIVES if state["node_status"].get(p) == "done"}
    second = second_evidence_check({**state, "missing_evidence": rows})["missing_evidence"]
    unrun = []
    for row in second:
        if row["stage"] == 2 and row["perspective"] in done:
            reworked = state["rework_counts"].get(row["perspective"], 0) >= MAX_REWORK
            rows.append({**row, "status": "recorded" if reworked else "pending"})
        elif row["stage"] == 2:
            unrun.append(row)
    return rows, unrun


def _needs_run(state, name):
    return state["node_status"].get(name) in (None, "stale")


def _can_retry_failure(state, name):
    return state["node_status"].get(name) == "failed" and state["rework_counts"].get(name, 0) < MAX_REWORK


def _final_steps(state):
    """마무리에 남겨둘 결정 수: 아직 실행할 마무리 단계 + 종료 결정."""
    return sum(_needs_run(state, n) or _can_retry_failure(state, n) for n in FINAL_STAGES) + 1


def choose_perspective(state, candidates, missing, llm):
    """후보 관점 중 다음 실행 대상을 State 요약 기반으로 LLM이 고른다. 실패·범위 밖이면 공백이 큰 관점."""
    gaps = {p: sum(x["stage"] == 2 and x["perspective"] == p for x in missing) for p in candidates}
    fallback = max(candidates, key=lambda p: gaps[p])
    if len(candidates) == 1 or llm is None:
        return fallback, "유일 후보" if len(candidates) == 1 else "근거 공백 최대"
    summary = {
        "candidates": [
            {
                "perspective": p,
                "status": state["node_status"].get(p) or "미수집",
                "criteria": list(EVALUATION_CRITERIA[p]),
                "missing_criteria": gaps[p],
            }
            # 선언 순서가 아니라 근거 공백이 큰 순서로 제시한다.
            for p in sorted(candidates, key=lambda p: -gaps[p])
        ],
        "collected": {
            p: len(state[f"{p}_analysis"].get("findings", [])) for p in PERSPECTIVES if p not in candidates
        },
        "technical_evidence": len(state["technical_evidence"]),
        "technical_gaps": sum(x["stage"] == 1 for x in missing),
    }
    try:
        choice = llm.generate(
            "route",
            "Supervisor로서 다음에 실행할 관점 에이전트 하나를 candidates 중에서 고른다. "
            "이미 수집된 결과와 근거 공백을 보고, 지금 보완 효과가 가장 큰 관점을 고르고 이유를 한 문장으로 쓴다.",
            summary,
            RouteChoice,
        )
    except Exception as exc:  # noqa: BLE001 -- 라우팅 LLM 실패가 전체 실행을 멈추지 않게 한다
        get_logger().warning("ROUTE_FALLBACK | %s | 위치=%s", type(exc).__name__, error_location(exc))
        return fallback, "라우팅 LLM 실패, 근거 공백 최대"
    if choice.target not in candidates:
        return fallback, "후보 밖 선택 무시, 근거 공백 최대"
    return choice.target, choice.reason


def decide(state, llm=None):
    """(target, reason, missing_items, 추가 업데이트)를 돌려준다. 위에서부터 처음 맞는 규칙을 쓴다."""
    status, counts = state["node_status"], dict(state["rework_counts"])
    missing, unrun = _missing_evidence(state)
    updates = {"missing_evidence": missing}
    remaining = state["max_steps"] - state["step_count"]  # 이번 결정을 포함해 남은 결정 수

    def rework(name, reason, items=(), stale=()):
        counts[name] = counts.get(name, 0) + 1
        node_status = {**status, **{x: "stale" for x in stale}}
        return name, reason, list(items), {**updates, "rework_counts": counts, "node_status": node_status}

    # 1. 종료 가드: 남은 결정이 마무리에 필요한 만큼뿐이면 조사를 멈추고 마무리 단계로 간다.
    if remaining <= 0:
        return END_TARGET, "step_limit: 상한 도달로 종료", [], updates
    if remaining <= _final_steps(state):
        # 끝내지 못한 조사(미실행 관점 포함)는 보고서 한계에 남도록 step_limit으로 기록한다.
        updates["missing_evidence"] = [
            {**x, "status": "step_limit"} if x["status"] == "pending" else x for x in missing
        ] + [{**x, "status": "step_limit"} for x in unrun]
        for name in FINAL_STAGES:
            if _needs_run(state, name):
                return name, f"step_limit: 조사 중단, {name} 진행", [], updates
            if _can_retry_failure(state, name):
                return rework(name, f"step_limit: {name} 실패 재시도")
        return END_TARGET, "step_limit: 마무리 완료 후 종료", [], updates

    # 2. 기술 근거: 미수집이면 수집, 부족하고 재검색이 남았으면 부족 항목만 재작업.
    if _needs_run(state, "technical"):
        return "technical", "기술 근거 미수집", [], updates
    if _can_retry_failure(state, "technical"):
        return rework("technical", "기술 조사 실패 재시도")
    first_gaps = [x for x in missing if x["stage"] == 1]
    if first_gaps and state["retry_count"] < state["max_retries"]:
        items = [{"technology": x["technology"], "item": x["item"]} for x in first_gaps]
        return "technical", f"기술 근거 부족 {len(items)}건 재검색", items, updates

    # 3. 아직 수집되지 않았거나 실패한 관점. 여럿이면 State를 보고 다음 관점을 고른다.
    pending = [p for p in PERSPECTIVES if _needs_run(state, p) or _can_retry_failure(state, p)]
    if pending:
        target, why = choose_perspective(state, pending, missing + unrun, llm)
        if _can_retry_failure(state, target):
            return rework(target, f"관점 에이전트 실패 재시도 ({why})")
        return target, f"관점 미수집 ({why})", [], updates

    # 4. 근거가 부족한 관점은 부족 기준만 재작업을 요청한다.
    gaps = {
        p: [x for x in missing if x["stage"] == 2 and x["perspective"] == p]
        for p in PERSPECTIVES
        if counts.get(p, 0) < MAX_REWORK and status.get(p) == "done"
    }
    gaps = {p: rows for p, rows in gaps.items() if rows}
    if gaps:
        target, why = choose_perspective(state, list(gaps), missing, llm)
        items = [{"technology": x["technology"], "criterion": x["item"]} for x in gaps[target]]
        return rework(target, f"{target} 근거 부족 {len(items)}건 재조사 ({why})", items)

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

    # 9. 품질 미달이면 실패 항목에 맞춰 재작업한다. 재작업 후 마무리까지 갈 결정이 남아 있을 때만.
    verdict = state["eval_result"]
    if verdict and not verdict["passed"] and counts.get("quality", 0) < MAX_QUALITY_LOOPS:
        failed = {name: item for name, item in verdict["items"].items() if not item["passed"]}
        items = [{"criterion": name, "reason": item["reason"]} for name, item in failed.items()]
        empty = [p for p in PERSPECTIVES if not state[f"{p}_analysis"].get("findings")]
        if "coverage" in failed and empty:
            # 비어 있는 관점을 처음부터 다시 조사하고, 그 결과를 쓰는 후속 단계도 다시 실행한다.
            target, stale, items = empty[0], (empty[0], *DOWNSTREAM["perspective"]), []
        else:
            target, stale = "report", DOWNSTREAM["report"]
        if remaining >= len({target, *stale}) + 1:  # 재작업 대상들 + 종료 결정
            counts["quality"] = counts.get("quality", 0) + 1
            node_status = {**status, **{x: "stale" for x in stale}}
            reason = "품질 미달 재작업: " + ", ".join(failed)
            return target, reason, items, {**updates, "rework_counts": counts, "node_status": node_status}

    # 10. 종료.
    passed = verdict["passed"] if verdict else False
    return END_TARGET, "품질 평가 통과" if passed else "품질 미달, 재작업 상한 도달로 종료", [], updates


def supervisor_node(state, services=None):
    target, reason, items, updates = decide(state, services.llm if services else None)
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
