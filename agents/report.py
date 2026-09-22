"""보고서 Agent: State 자료로 작성하고 실제 인용된 참고문헌만 렌더링."""

import json
import re

from config import PERSPECTIVES
from evidence import all_evidence, collect_references, valid_ids
from state import technology_names
from workflow_logging import get_logger

SOURCE_TIER_LABELS = {
    1: "논문",
    2: "표준·공식 기술문서",
    3: "특허",
    4: "시장·제품 공시",
    5: "2차 자료",
}


SECTIONS = {
    "1.1": "데이터센터 환경의 KV Cache 문제",
    "1.2": "분석 목적 및 범위",
    "2.1": "ITME",
    "2.2": "CXL-PIM",
    "2.3": "기술 선정 및 비교 이유",
    "3.1": "ITME 구조, 실험 조건, 주요 결과와 한계",
    "3.2": "CXL-PIM 구조, 실험 조건, 주요 결과와 한계",
    "3.3": "두 기술의 구조적 차이와 직접 비교 한계",
    "4.1": "기술 성숙도",
    "4.2": "시장성",
    "4.3": "이해관계자",
    "4.4": "데이터센터·클라우드 적용성",
    "5.1": "공통적으로 확인된 사항",
    "5.2": "관점에 따라 평가가 엇갈리는 지점",
    "5.3": "주요 Trade-off",
    "5.4": "종합 의견",
    "6.1": "공개 정보 기반 분석의 한계",
    "6.2": "서로 다른 실험 환경에 따른 직접 비교 한계",
    "6.3": "확증편향 방지 및 근거 검증 방법",
}
CHAPTERS = {
    "1": "분석 배경 및 문제 정의",
    "2": "평가 대상 기술 선정",
    "3": "기술 개요",
    "4": "다관점 평가",
    "5": "관점 간 종합 및 시사점",
    "6": "분석 한계 및 신뢰성 확보",
}


def _reference_text(index, ref):
    author = ref["author"] or "저자·기관 정보 미확인"
    date = str(ref["year"] or ref["published_date"] or "발행일 미확인")
    if ref.get("source_type") == "web":
        site = ref.get("site_name") or "사이트명 미확인"
        return f"{index}. {author} ({date}). {ref['source']}. {site}, {ref['source_url']}"
    publication = ", ".join(x for x in (ref.get("venue"), ref.get("identifier")) if x)
    publication = publication or "학술지·학회명 미확인"
    return f"{index}. {author} ({date}). {ref['source']}. {publication}, {ref['source_url']}"


def synthesis_fallback(state):
    """Synthesis가 검증에서 탈락했을 때 5장을 채울 재료.

    새 내용을 만들지 않고, 이미 검증된 4장 finding과 conflicts만 재조합한다.
    """
    findings = [
        finding
        for perspective in PERSPECTIVES
        for finding in state[f"{perspective}_analysis"].get("findings", [])
    ]
    by_criterion = {}
    for finding in findings:
        by_criterion.setdefault(finding["criterion"], []).append(finding)
    common = [
        {
            "text": f"두 기술 모두 {criterion} 관점에서 공개 근거가 확인되었다. "
            + " / ".join(f"{x['technology']}: {x['claim']}" for x in rows),
            "evidence_ids": list(dict.fromkeys(eid for row in rows for eid in row["evidence_ids"])),
        }
        for criterion, rows in by_criterion.items()
        if len({x["technology"] for x in rows}) > 1
    ]
    differences = [
        {
            "text": conflict["description"] + " " + conflict.get("implication", ""),
            "evidence_ids": conflict["evidence_ids"],
        }
        for conflict in state["conflicts"]
    ]
    tradeoffs = list(differences)
    conclusion = []
    if findings:
        representative = []
        for technology in technology_names(state):
            representative.extend(
                next((x["evidence_ids"] for x in findings if x["technology"] == technology), [])
            )
        conclusion = [
            {
                "text": (
                    "확인된 근거 범위에서는 단일한 우위를 확정하기보다, 메모리 용량 확장, "
                    "데이터 이동, 구현 복잡도와 공개 검증 수준을 함께 고려해야 한다. "
                    "공개되지 않은 항목은 부정적 결과가 아니라 판단 범위의 한계로 해석한다."
                ),
                "evidence_ids": list(dict.fromkeys(representative)),
            }
        ]
    return {"5.1": common, "5.2": differences, "5.3": tradeoffs, "5.4": conclusion}


def _unique_lines(lines, limit=None):
    result, seen = [], set()
    for line in lines:
        key = re.sub(r"\s+", " ", line).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(line)
        if limit and len(result) >= limit:
            break
    return result


def _gap_summary(gaps):
    grouped = {}
    for gap in gaps:
        key = (gap["technology"], gap.get("perspective", "technical"))
        grouped.setdefault(key, []).append(gap["item"])
    labels = {
        "technical": "기술 조사",
        "trl": "기술 성숙도",
        "market": "시장성",
        "stakeholder": "이해관계자",
        "domain": "데이터센터 적용성",
    }
    return [
        (
            f"- {technology}의 {labels.get(perspective, perspective)}에서 공개 직접 근거가 제한된 항목: "
            f"{', '.join(dict.fromkeys(items))}. 이는 부정적 판정이 아니라 현재 공개 자료로 확정할 수 있는 "
            "범위의 한계이며, 관련 생태계·구조적 근거는 해당 평가 절에 별도로 제시했다."
        )
        for (technology, perspective), items in grouped.items()
    ]


def _market_context_lines(state, technologies):
    """직접 제품화·도입 근거가 없는 항목만 제한 사항으로 표시한다."""
    findings = state["market_analysis"].get("findings", [])
    lines = []
    if any(finding.get("evidence_scope") == "ecosystem" for finding in findings):
        lines.append(
            "CXL·KV Cache 상위 시장과 생태계 근거는 개별 기술의 "
            "제품화·실제 도입 Fact와 분리해 해석한다."
        )
    criteria = ("제품화", "실제 도입")
    for technology in technologies:
        confirmed = {
            finding.get("criterion")
            for finding in findings
            if finding.get("technology") == technology
            and finding.get("evidence_scope") == "direct"
        }
        missing = [criterion for criterion in criteria if criterion not in confirmed]
        if missing:
            lines.append(
                f"{technology}의 공개 직접 근거가 제한된 시장성 항목: "
                f"{'·'.join(missing)}. 이는 현재 검색·검증된 자료 범위의 한계이다."
            )
    return lines


def trl_levels(state, technology):
    """한 기술에 대해 기준별로 판정된 TRL 값 집합."""
    return {
        finding["trl_level"]
        for finding in state["trl_analysis"].get("findings", [])
        if finding["technology"] == technology and finding.get("trl_level") is not None
    }


def trl_summary(state, technologies):
    """기준별 TRL 판정이 갈리면 단일 값으로 확정하지 않고 범위로 제시한다."""
    lines = []
    for technology in technologies:
        levels = {}
        for finding in state["trl_analysis"].get("findings", []):
            if finding["technology"] == technology and finding.get("trl_level") is not None:
                levels.setdefault(finding["trl_level"], []).append(finding.get("criterion", "기준 미상"))
        if not levels:
            lines.append(f"- {technology}: 공개 자료로 TRL을 추정할 근거가 부족하다.")
            continue
        low, high = min(levels), max(levels)
        if low == high:
            lines.append(f"- {technology}: 추정 TRL {low}")
            continue
        basis = ", ".join(
            f"TRL {level}({', '.join(dict.fromkeys(criteria))})" for level, criteria in sorted(levels.items())
        )
        lines.append(
            f"- {technology}: 추정 TRL {low}~{high}. 평가 기준에 따라 판정이 갈렸다({basis}). "
            "연구·프로토타입 수준 근거와 실환경에 가까운 실증 근거가 함께 확인되어 판단 경계에 있으므로 "
            "단일 값으로 확정하지 않고 범위로 제시한다."
        )
    return lines


def _render_report(state, draft, *, mode="live"):
    evidence = all_evidence(state)
    technologies = technology_names(state)
    used = set()

    def cited(paragraph):
        if not paragraph["text"].strip() or not valid_ids(paragraph["evidence_ids"], evidence):
            return ""
        ids = list(dict.fromkeys(paragraph["evidence_ids"]))
        used.update(ids)
        return paragraph["text"].strip() + " ⟦CITE:" + "|".join(ids) + "⟧"

    supplemental = {section_id: [] for section_id in SECTIONS}
    section_limitations = {section_id: [] for section_id in SECTIONS}
    fallback = synthesis_fallback(state)
    for index, selection in enumerate(state["technologies"], 1):
        supplemental[f"2.{index}"].append(
            f"- {selection['name']} ({selection['category']}): {selection['key_approach']} - "
            f"{selection['selection_reason']}"
        )
    section_by_perspective = {"trl": "4.1", "market": "4.2", "stakeholder": "4.3", "domain": "4.4"}
    for perspective, section_id in section_by_perspective.items():
        analysis = state[f"{perspective}_analysis"]
        for finding in analysis.get("findings", []):
            label = f"[{finding['kind']}] {finding['technology']}: {finding['claim']}"
            if finding.get("evidence_scope") == "ecosystem":
                label += " (상위 시장·생태계 근거를 이용한 제한적 해석)"
            elif finding.get("evidence_scope") == "comparison":
                label += " (비교 기술 근거를 이용한 구조적 해석)"
            if finding.get("trl_level") is not None:
                spread = trl_levels(state, finding["technology"])
                label += (
                    f" (공개 정보 기반 추정 TRL {min(spread)}~{max(spread)} 중 "
                    f"{finding.get('criterion', '해당 기준')} 판정 {finding['trl_level']})"
                    if len(spread) > 1
                    else f" (공개 정보 기반 추정 TRL {finding['trl_level']})"
                )
            if perspective == "stakeholder":
                speakers = []
                for eid in finding["evidence_ids"]:
                    item = evidence.get(eid, {})
                    if item.get("speaker") or item.get("affiliation"):
                        speakers.append(
                            " / ".join(x for x in (item.get("speaker"), item.get("affiliation")) if x)
                        )
                if speakers:
                    label += " (발언자·소속: " + ", ".join(dict.fromkeys(speakers)) + ")"
            text = cited({"text": label, "evidence_ids": finding["evidence_ids"]})
            if text:
                supplemental[section_id].append("- " + text)
                if finding.get("limitation"):
                    section_limitations[section_id].append(
                        f"- {finding['technology']} 평가 한계: {finding['limitation']}"
                    )
        section_limitations[section_id].extend(
            "- 공통 평가 한계: " + limitation for limitation in analysis.get("limitations", [])
        )

    for section_id, limitations in section_limitations.items():
        supplemental[section_id].extend(_unique_lines(limitations, limit=4))

    technical_items = set()
    for eid, item in state["technical_evidence"].items():
        if item["technology"] not in technologies:
            continue
        section_id = "3.1" if item["technology"] == technologies[0] else "3.2"
        item_key = (item["technology"], item.get("item"))
        if item_key in technical_items:
            continue
        technical_items.add(item_key)
        condition = item.get("experimental_condition")
        description = f"{item.get('item', '기술 근거')}: {item['claim']}"
        if item.get("numeric") and condition:
            description += f" / 적용된 실험 조건: {condition}"
        text = cited(
            {
                "text": description,
                "evidence_ids": [eid],
            }
        )
        if text:
            supplemental[section_id].append("- " + text)

    first_by_technology = {
        technology: next(
            (item for item in state["technical_evidence"].values() if item["technology"] == technology),
            None,
        )
        for technology in technologies
    }
    background = [item for item in first_by_technology.values() if item]
    if background:
        text = cited(
            {
                "text": (
                    "장문맥 LLM 추론의 KV Cache 문제를 대상으로, 외부 계층형 메모리 확장과 "
                    "메모리 근접 연산이 용량·데이터 이동·구축 복잡도에 미치는 영향을 비교한다."
                ),
                "evidence_ids": [item["evidence_id"] for item in background],
            }
        )
        if text:
            supplemental["1.1"].append(text)

    if len(background) == len(technologies):
        text = cited(
            {
                "text": (
                    f"{technologies[0]}는 {state['technologies'][0]['key_approach']}을 중심으로 하고, "
                    f"{technologies[1]}은 {state['technologies'][1]['key_approach']}을 중심으로 한다. "
                    "따라서 동일 조건의 단순 성능 서열보다 저장 위치, 연산 위치와 데이터 이동 경로의 차이를 "
                    "중심으로 해석해야 한다."
                ),
                "evidence_ids": [item["evidence_id"] for item in background],
            }
        )
        if text:
            supplemental["3.3"].append(text)

    for counter in state["counter_evidence"].values():
        if counter["status"] == "found":
            counter_eid = counter["evidence"]["evidence_id"]
            text = cited(
                {
                    "text": "반대·제약 근거: " + counter["counter_claim"],
                    "evidence_ids": [counter_eid],
                }
            )
            if text:
                supplemental["6.3"].append("- " + text)
        else:
            # 개별 실패 문장을 반복하지 않는다. 검색 범위와 채택 수는
            # 아래 6.3 검증 요약에서 한 번만 설명한다.
            continue

    for conflict in state["conflicts"]:
        description = conflict["description"]
        if conflict.get("conditions"):
            description += " 조건: " + conflict["conditions"]
        description += " 해석: " + conflict["implication"]
        text = cited({"text": description, "evidence_ids": conflict["evidence_ids"]})
        if text:
            supplemental["5.2"].append("- 상충·비교 제한: " + text)

    lines = ["# ITME와 CXL-PIM 데이터센터 적용성 비교 평가", ""]
    if mode != "live":
        lines.extend(["> DEMO / 테스트용 가상 근거입니다. 기술 평가나 제출 보고서로 사용하지 마세요.", ""])
    lines.extend(["## SUMMARY", ""])
    # SUMMARY 길이는 글자 기준 700자 이내; 실제 반 페이지 여부는 편집 단계에서 확인.
    length = 0
    for paragraph in state["synthesis"].get("summary", []):
        if length + len(paragraph["text"]) > 700:
            continue
        text = cited(paragraph)
        if text:
            lines.extend([text, ""])
            length += len(paragraph["text"])
    if not length:
        candidates = state["synthesis"].get("conclusion", []) or fallback.get("5.4", [])
        for paragraph in candidates[:1]:
            text = cited(paragraph)
            if text:
                lines.extend([text, ""])
                length += len(paragraph["text"])
        if not length:
            lines.extend(
                [
                    (
                        "공개 근거의 범위와 품질을 우선 확인했으며, 확인되지 않은 항목은 기술의 실패가 "
                        "아니라 현재 자료로 판단할 수 없는 범위로 분리했다."
                    ),
                    "",
                ]
            )
    content = {}
    for section in draft.get("sections", []):
        if section["section_id"] in SECTIONS:
            content.setdefault(section["section_id"], []).extend(section["paragraphs"])
    for section_id, title in SECTIONS.items():
        if section_id.endswith(".1"):
            chapter = section_id.split(".")[0]
            lines.extend([f"## {chapter}. {CHAPTERS[chapter]}", ""])
        lines.extend([f"### {section_id} {title}", ""])
        paragraphs = [cited(p) for p in content.get(section_id, [])]
        paragraphs = [p for p in paragraphs if p]
        if section_id == "1.2":
            paragraphs.insert(
                0, f"분석 범위는 {state['domain']}에서 ITME와 CXL-PIM의 KV Cache 관리 방식이다."
            )
        elif section_id == "2.3":
            paragraphs.insert(
                0, "비교 기술은 설계서에서 사람이 선정했으며 자동 기술 선정 Agent는 사용하지 않았다."
            )
        elif section_id == "4.1":
            paragraphs = trl_summary(state, technologies) + paragraphs
            paragraphs.append(
                "TRL은 공개 정보 기반 추정이며 공식 인증값이 아니다. "
                "아래 개별 항목의 TRL은 각 평가 기준에서 판정한 값이며, "
                "기준 간 차이가 있는 경우 위의 통합 범위를 기준으로 해석한다."
            )
        elif section_id == "4.2":
            paragraphs = _market_context_lines(state, technologies) + paragraphs
        elif section_id == "6.1":
            paragraphs.extend(_gap_summary(state["missing_evidence"]))
            low_tier = sum(
                item.get("role") == "web" and item.get("source_tier", 5) == 5 for item in evidence.values()
            )
            if low_tier:
                paragraphs.append(
                    f"- 2차 웹 자료 {low_tier}건은 개별 기술의 직접 Fact가 아니라 업계 관측·생태계 맥락과 "
                    "제한적 Inference에만 사용했다. 제품화·도입 판단은 논문·공식 자료와 분리했다."
                )
        elif section_id == "6.2":
            paragraphs.append(
                "GPU, 모델, Context Length, Batch, Baseline이 다르거나 확인되지 않은 "
                "수치는 동일 조건의 벤치마크로 해석하지 않는다."
            )
        elif section_id == "6.3":
            found = sum(x["status"] == "found" for x in state["counter_evidence"].values())
            paragraphs.append(
                f"기술 재검색 {state['retry_count']}회, 주요 주장별 반대 근거 검색 "
                f"{len(state['counter_evidence'])}회, 반대 근거 채택 {found}건. "
                "반대 근거 미발견은 원 주장의 참을 입증하지 않는다. "
                "인용문 일치는 자동 확인했으나 주장과 인용의 의미적 일치에는 사람의 검토가 필요하다."
            )
        paragraphs.extend(supplemental[section_id])
        paragraphs = _unique_lines(paragraphs)
        if not paragraphs and fallback.get(section_id):
            # Synthesis가 탈락한 절은 이미 검증된 finding·conflict로 재조합한다.
            get_logger().info(
                "SECTION_FALLBACK | section=%s | items=%d", section_id, len(fallback[section_id])
            )
            paragraphs = ["아래 내용은 4장의 검증된 평가 결과를 재구성한 것이다."]
            paragraphs.extend(text for item in fallback[section_id] if (text := cited(item)))
        if not paragraphs:
            paragraphs = [
                (
                    "이 절의 세부 항목을 단독으로 확정할 직접 근거는 제한적이다. "
                    "확인된 기술 구조와 인접 평가 결과는 다른 절에 제시하고, 자료 공백이 결론의 우열로 "
                    "해석되지 않도록 분석 범위를 제한했다."
                )
            ]
        lines.extend(paragraphs)
        lines.append("")

    refs = collect_references({eid: evidence[eid] for eid in sorted(used)})
    by_eid = {eid: index for index, ref in enumerate(refs, 1) for eid in ref["evidence_ids"]}
    lines.extend(["", "## REFERENCE", ""])
    for index, ref in enumerate(refs, 1):
        tier = min(
            (evidence[eid].get("source_tier", 5) for eid in ref["evidence_ids"] if eid in evidence),
            default=5,
        )
        lines.append(_reference_text(index, ref) + f" [출처 등급 {tier}: {SOURCE_TIER_LABELS[tier]}]")
        lines.append("   - 근거 ID: " + ", ".join(ref["evidence_ids"]))

    def replace_citation(match):
        markers = []
        for eid in match.group(1).split("|"):
            page = evidence[eid].get("page")
            marker = str(by_eid[eid]) + (f", p.{page}" if page is not None else "")
            if marker not in markers:
                markers.append(marker)
        return "[" + "; ".join(markers) + "]"

    report = re.sub(r"⟦CITE:([^⟧]+)⟧", replace_citation, "\n".join(lines) + "\n")
    get_logger().info(
        "REPORT_CITATIONS | cited_evidence=%d | reference_sources=%d",
        len(used),
        sum(any(e in used for e in r["evidence_ids"]) for r in refs),
    )
    return report, refs


def render_report(state, draft, *, mode="live"):
    """기존 호출부와 호환되는 Markdown 렌더러."""
    report, _ = _render_report(state, draft, mode=mode)
    return report


def contents_writer(state, services):
    """수업 Report Agent의 contents_writer에 해당하는 근거 기반 작성 단계."""
    if services.report_writer is None:
        raise ValueError("report_writer 서비스 필요")
    payload = {
        "sections": SECTIONS,
        "technologies": state["technologies"],
        "domain": state["domain"],
        # 원문 청크와 인용문은 넘기지 않는다. 검증된 결과만 전달해 재작성을 막는다.
        "evidence": {
            eid: {
                k: v
                for k, v in item.items()
                if k
                in (
                    "evidence_id",
                    "technology",
                    "perspective",
                    "item",
                    "claim",
                    "kind",
                    "source",
                    "page",
                    "scope",
                )
            }
            for eid, item in all_evidence(state).items()
        },
        "synthesis": state["synthesis"],
        "analyses": {p: state[f"{p}_analysis"] for p in PERSPECTIVES},
        "conflicts": state["conflicts"],
        "counter_evidence": state["counter_evidence"],
        "missing_evidence": state["missing_evidence"],
    }
    return services.report_writer.invoke(
        {"data": json.dumps(payload, ensure_ascii=False), "payload": payload}
    )


def report_generator(state, draft, *, mode="live"):
    """수업 Report Agent의 최종화 단계: 본문과 실제 인용 출처를 함께 반환한다."""
    report, references = _render_report(state, draft, mode=mode)
    return {"final_report": report, "references": references}


def report_agent(state, services):
    draft = contents_writer(state, services)
    return report_generator(state, draft.model_dump(), mode=services.mode)
