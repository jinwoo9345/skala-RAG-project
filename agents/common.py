"""4개 관점 평가의 공통 검증. 역할/자료 선택은 각 Agent에서 정한다."""

from config import (
    EVALUATION_CRITERIA,
    EVALUATION_GUIDANCE,
    FACT_MAX_TIER,
    MARKET_LEVEL_CRITERIA,
    QUERY_TERMS,
    TECH_PROFILES,
    TECHNICAL_FACT_MAX_TIER,
)
from evidence import extract_evidence, valid_ids
from schemas import Evaluation
from state import technology_names
from tools.web_search import relevance, web_search
from workflow_logging import get_logger

DIRECT_EVIDENCE_CRITERIA = {
    "논문·PoC·Prototype",
    "실환경 검증",
    "상용 제품 여부",
    "제품화",
    "실제 도입",
    "도입 기업·개발자",
}


def _scope(item):
    if item.get("role") != "web":
        return "direct" if item.get("role") == "core" else "comparison"
    return item.get("scope", "ecosystem")


def _finding_policy(item, selected, perspective):
    """출처가 존재한다는 사실과 주장에 사용할 수 있다는 사실을 구분한다."""
    scopes = {_scope(evidence) for evidence in selected}
    item["evidence_scope"] = (
        "direct" if "direct" in scopes else "ecosystem" if "ecosystem" in scopes else "comparison"
    )
    max_tier = TECHNICAL_FACT_MAX_TIER if perspective in ("technical", "domain", "trl") else FACT_MAX_TIER
    trusted_fact = any(
        evidence.get("role") != "web"
        or (evidence.get("kind") == "Fact" and evidence.get("source_tier", 5) <= max_tier)
        for evidence in selected
    )
    if item["kind"] == "Fact" and not trusted_fact:
        return False, "낮은 등급의 웹 자료만으로 작성된 Fact를 제외함."
    if item["criterion"] in DIRECT_EVIDENCE_CRITERIA and "direct" not in scopes:
        # 제품화·도입은 개별 기술에 대한 주장이므로, 상위 시장 자료로는 어떤 종류의 주장도 만들지 않는다.
        return False, "개별 기술의 직접 근거가 없는 제품화·도입 주장을 제외함."
    return True, ""


def evaluate(state, services, perspective, sources, evidence=None):
    technologies = technology_names(state)
    evidence = dict(evidence or {})
    for technology, chunks in sources.items():
        evidence.update(
            extract_evidence(
                services,
                chunks,
                technology=technology,
                perspective=perspective,
                items=EVALUATION_CRITERIA[perspective],
            )
        )
    result = services.llm.generate(
        "evaluate:" + perspective,
        "지정 관점 및 criteria별로 기술을 평가하되 근거 있는 항목만 findings로 반환. "
        "기술별 모든 criteria를 검토하고 미확인 사항은 limitations에 기록. "
        "criterion은 criteria 중 하나. 다른 기술의 근거 ID를 사용할 때 직접 성능 근거로 오인 금지. "
        "TRL 관점에서만 trl_level을 1~9 또는 null로 기입. 검증 근거가 없으면 null. "
        "TRL 1 기초원리, 2 개념, 3 개념증명, 4 실험실 검증, 5 관련환경 검증, "
        "6 관련환경 시제품, 7 운영환경 시제품, 8 시스템 완성/검증, 9 실제 운영 입증. "
        "연구용 테스트베드·시뮬레이션은 실환경 검증으로 보지 않는다. "
        "TRL 값은 공개 정보 기반 추정이라고 limitation에 명시. "
        "시장 평가는 개별 기술의 채택 근거와 상위 CXL/PIM 시장 자료를 같은 근거처럼 섞지 말 것. "
        "이해관계자 평가는 실제 발언(Opinion)과 Agent 해석(Inference)을 구분하고 발언자·소속·발행일을 확인할 것. "
        "기술 문서의 주장을 외부 검증으로 간주하지 말 것. 모든 finding에는 evidence_ids 필요.",
        {
            "perspective": perspective,
            "technologies": technologies,
            "technology_selection": state["technologies"],
            "domain": state["domain"],
            "criteria": list(EVALUATION_CRITERIA[perspective]),
            "criterion_guidance": EVALUATION_GUIDANCE[perspective],
            "evidence": evidence,
        },
        Evaluation,
        judge=perspective == "trl",
    )
    findings, limitations = [], list(result.limitations)
    for finding in result.findings:
        item = finding.model_dump()
        if (
            item["technology"] not in technologies
            or item["criterion"] not in EVALUATION_CRITERIA[perspective]
            or not item["claim"].strip()
            or not valid_ids(item["evidence_ids"], evidence)
            or not any(evidence[i]["technology"] == item["technology"] for i in item["evidence_ids"])
        ):
            limitations.append("근거 ID 또는 평가 항목 검증 실패로 일부 주장을 제외함.")
            continue
        selected = [evidence[eid] for eid in item["evidence_ids"]]
        accepted, policy_note = _finding_policy(item, selected, perspective)
        if not accepted:
            limitations.append(policy_note)
            continue
        if perspective != "trl":
            item["trl_level"] = None
        elif item["trl_level"] is not None:
            if all(evidence.get("role") != "web" for evidence in selected):
                # 논문 근거만으로는 실험실 검증(TRL 4)을 넘는다고 판단하지 않는다.
                item["trl_level"] = min(item["trl_level"], 4)
            item["limitation"] = "공개 정보 기반 추정 TRL이며 공식 인증값이 아님. " + item["limitation"]
            item["kind"] = "Inference"
        findings.append(item)
    used = {i for finding in findings for i in finding["evidence_ids"]}
    missing = [
        {
            "technology": technology,
            "criterion": criterion,
            "reason": "직접 근거 미공개 또는 검증된 평가 주장·연결 근거 부족",
        }
        for technology in technologies
        for criterion in EVALUATION_CRITERIA[perspective]
        if not any(
            finding["technology"] == technology and finding["criterion"] == criterion for finding in findings
        )
    ]
    get_logger().info(
        "EVALUATION_READY | perspective=%s | findings=%d | evidence=%d", perspective, len(findings), len(used)
    )
    return {
        f"{perspective}_analysis": {
            "perspective": perspective,
            "findings": findings,
            "evidence": {i: evidence[i] for i in sorted(used)},
            "missing_evidence": missing,
            "limitations": list(dict.fromkeys(limitations)),
        }
    }


def web_sources(state, services, perspective):
    """기술 × 평가 기준 단위로 검색한다. 기본 1회, 결과가 없을 때만 대체 질의 1회."""
    sources = {}
    for technology in technology_names(state):
        profile = TECH_PROFILES.get(technology, {})
        primary = (list(profile.get("distinctive", ())) or [technology])[0]
        alias = profile.get("search_alias", technology)
        rows, seen = [], set()
        for criterion in EVALUATION_CRITERIA[perspective]:
            terms = QUERY_TERMS.get(criterion, criterion)
            if criterion in MARKET_LEVEL_CRITERIA and profile.get("market_term"):
                # 연구 단계 기술은 개별 시장 자료가 없으므로 상위 시장 단위로 검색한다.
                queries = (f"{profile['market_term']} {terms}", f"{alias} {terms}")
            else:
                queries = (f"{primary} {terms}", f"{alias} KV cache {terms}")
            for query in queries:
                relevant = 0
                for candidate in web_search(
                    services.web, query, max_results=services.settings.search_results
                ):
                    scope = (
                        "direct"
                        if services.mode == "demo"
                        else relevance(candidate["source"] + " " + candidate["content"], technology)
                    )
                    if scope is None:
                        continue
                    relevant += 1
                    # 이미 수집한 자료는 다시 담지 않되, 기준이 충족된 것으로 본다.
                    if candidate["chunk_id"] in seen:
                        continue
                    seen.add(candidate["chunk_id"])
                    rows.append({**candidate, "scope": scope, "criterion": criterion})
                if relevant:
                    break
        sources[technology] = rows
    return sources
