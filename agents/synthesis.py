"""종합 Agent: State만 사용, 새로운 검색/평가 지표를 생성하지 않는다."""

from config import PERSPECTIVES
from evidence import all_evidence, valid_ids
from schemas import Synthesis
from workflow_logging import get_logger


def synthesis_agent(state, services):
    evidence = all_evidence(state)
    result = services.llm.generate(
        "synthesis",
        "State의 평가/반대근거/상충/부족근거를 종합해 summary, commonalities, differences, tradeoffs, "
        "conclusion을 작성. 새로운 평가나 검색은 하지 않는다. "
        "commonalities, differences, tradeoffs, conclusion은 각각 최소 1개 이상 반드시 작성한다. "
        "evidence_ids는 available_evidence_ids에 있는 ID만 그대로 사용하고 새로 만들지 않는다. "
        "근거가 부족한 항목은 4개 관점 findings의 evidence_ids를 재사용해 작성하되 "
        "확실성을 낮춰 서술한다. 반대 근거 not_found는 원 주장 확인을 뜻하지 않는다. "
        "특정 기술을 추천하거나 승자를 정하지 않는다. summary는 한국어 500자 내외.",
        {
            "available_evidence_ids": sorted(evidence),
            "analyses": {p: state[f"{p}_analysis"] for p in PERSPECTIVES},
            "counter_evidence": state["counter_evidence"],
            "conflicts": state["conflicts"],
            "missing_evidence": state["missing_evidence"],
        },
        Synthesis,
    ).model_dump()
    for key in ("summary", "commonalities", "differences", "tradeoffs", "conclusion"):
        before = len(result[key])
        result[key] = [x for x in result[key] if x["text"].strip() and valid_ids(x["evidence_ids"], evidence)]
        if before and not result[key]:
            get_logger().warning("SYNTHESIS_SECTION_EMPTY | key=%s | 후보 %d건 모두 탈락", key, before)
    result["missing_evidence"] = state["missing_evidence"]
    return {"synthesis": result}
