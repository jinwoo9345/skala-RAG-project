"""반대 근거는 주요 주장당 Web Search 1회. 검증 실패를 성공으로 꾸미지 않는다."""

import hashlib

from config import FACT_MAX_TIER, PERSPECTIVES, TECHNICAL_FACT_MAX_TIER
from evidence import all_evidence, numeric_supported, quantitative_claim, quote_exists, valid_ids
from schemas import Conflicts, CounterResult
from state import technology_names
from tools.web_search import relevance, web_search
from workflow_logging import get_logger


def _main_claims(state, perspective, limit):
    findings = state[f"{perspective}_analysis"].get("findings", [])
    # 각 기술의 첫 주장부터 선택하여 한 기술에 검색 예산이 몰리지 않게 한다.
    by_tech = [[f for f in findings if f["technology"] == tech] for tech in technology_names(state)]
    result = []
    for rank in range(max((len(x) for x in by_tech), default=0)):
        for group in by_tech:
            if rank < len(group):
                result.append(group[rank])
    return result[:limit]


def counter_evidence_node(state, services):
    counters = {}
    for perspective in PERSPECTIVES:
        findings = _main_claims(state, perspective, services.settings.max_claims_per_perspective)
        for finding in findings:
            target = finding["claim"]
            identifier = "counter-" + hashlib.sha256((perspective + target).encode()).hexdigest()[:16]
            sources = web_search(
                services.web,
                f"{finding['technology']} {target[:350]} limitations contrary evidence",
                max_results=services.settings.search_results,
            )
            if services.mode != "demo":
                sources = [
                    {**source, "scope": scope}
                    for source in sources
                    if (scope := relevance(source["source"] + " " + source["content"], finding["technology"]))
                    is not None
                ]
            result = (
                services.llm.generate(
                    "counter",
                    "target_claim을 실제 반박하거나 적용 범위를 제한하는 자료만 찾는다. "
                    "관련 없는 부정적 문장은 반대 근거가 아니다. found=false이면 나머지는 빈 문자열. "
                    "found=true이면 source_id=전달된 chunk_id, quote=원문 그대로, counter_claim=요약. "
                    "kind는 원문의 Fact/Opinion을 구분한다. 서로 다른 실험 조건이라면 "
                    "experimental_condition에 차이를 기록하고, 확인할 수 없으면 빈 문자열로 둔다.",
                    {"target_claim": target, "sources": sources},
                    CounterResult,
                    judge=True,
                )
                if sources
                else None
            )
            row = {
                "target_claim": target,
                "evidence_ids": finding["evidence_ids"],
                "perspective": perspective,
                "technology": finding["technology"],
                "counter_claim": "공개된 반대 근거를 확인하지 못함",
                "source": "",
                "page_or_url": "",
                "status": "not_found",
                "search_count": 1,
            }
            source = next((x for x in sources if result and x["chunk_id"] == result.source_id), None)
            numeric = bool(result and quantitative_claim(result.counter_claim))
            max_tier = TECHNICAL_FACT_MAX_TIER if perspective in ("trl", "domain") else FACT_MAX_TIER
            if (
                result
                and result.found
                and source
                and result.counter_claim.strip()
                and quote_exists(result.quote, source["content"])
                and numeric_supported(
                    result.counter_claim,
                    [{"quote": result.quote, "experimental_condition": result.experimental_condition}],
                )
                and (
                    services.mode == "demo"
                    or result.kind != "Fact"
                    or source.get("source_tier", 5) <= max_tier
                )
                and (not numeric or bool(result.experimental_condition.strip()))
            ):
                counter = {
                    **{k: v for k, v in source.items() if k != "content"},
                    "evidence_id": identifier,
                    "technology": finding["technology"],
                    "perspective": "counter",
                    "claim": result.counter_claim,
                    "quote": result.quote,
                    "item": "반대 근거",
                    "kind": result.kind,
                    "numeric": numeric,
                    "experimental_condition": result.experimental_condition,
                    "condition_source": {},
                    "speaker": "",
                    "affiliation": "",
                }
                row.update(
                    {
                        "status": "found",
                        "counter_claim": result.counter_claim,
                        "source": source["source"],
                        "page_or_url": source["source_url"],
                        "evidence": counter,
                    }
                )
            elif result and result.found:
                row["validation_note"] = "후보의 출처/직접 인용 검증에 실패하여 반대 근거로 채택하지 않음"
            counters[identifier] = row
            get_logger().info("COUNTER_RESULT | perspective=%s | status=%s", perspective, row["status"])
    return {"counter_evidence": counters}


def conflict_node(state, services):
    evidence = all_evidence(state)
    response = services.llm.generate(
        "conflict",
        "4개 관점 사이의 상충만 분석: 성능/비용, 용량/이동량, 연구/도입, 이해관계자, 실험조건. "
        "category, description, evidence_ids, conditions, implication을 작성한다. conditions에는 "
        "차이가 발생한 GPU·모델·Context Length·요청 수·Baseline 또는 이해관계 조건을 기록한다. "
        "근거 없는 상충을 만들어내지 않는다. "
        "GPU/모델/context/batch/baseline 조건이 다르거나 미확인이면 직접 수치 비교 불가라고 명시한다.",
        {
            "analyses": {p: state[f"{p}_analysis"] for p in PERSPECTIVES},
            "counter_evidence": state["counter_evidence"],
            "evidence": evidence,
        },
        Conflicts,
        judge=True,
    )
    conflicts = [x.model_dump() for x in response.conflicts if valid_ids(x.evidence_ids, evidence)]
    numeric = [e for e in state["technical_evidence"].values() if e.get("numeric")]
    if len({e["technology"] for e in numeric}) > 1:
        # 구조화되지 않은 조건 문자열만으로 동등한 benchmark임을 인정하지 않는다.
        conflicts.append(
            {
                "category": "실험 조건 비교 제한",
                "description": "기술별 수치는 각 논문의 실험 결과이며 동일 조건의 교차 벤치마크가 아님.",
                "conditions": "GPU, 모델, Context Length, 요청 수, Batch, Baseline이 논문별로 다르거나 확인되지 않음.",
                "implication": "각 논문의 자체 Baseline 대비 변화로만 해석; 원시 수치로 우열 판단 금지.",
                "evidence_ids": [e["evidence_id"] for e in numeric],
            }
        )
    get_logger().info("CONFLICT_RESULT | conflicts=%d", len(conflicts))
    return {"conflicts": conflicts}
