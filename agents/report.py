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
    """확인된 서지 항목만 쓴다."""
    author = ref["author"]
    date = str(ref["year"] or ref["published_date"] or "")
    byline = f"{author} ({date}). " if author and date else f"{author}. " if author else f"({date}). " if date else ""
    if ref.get("source_type") == "web":
        place = ", ".join(x for x in (ref.get("site_name"), ref["source_url"]) if x)
        return f"{index}. {byline}{ref['source']}. {place}"
    place = ", ".join(x for x in (ref.get("venue"), ref.get("identifier"), ref["source_url"]) if x)
    return f"{index}. {byline}{ref['source']}. {place}"


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


def _normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"⟦CITE:[^⟧]+⟧", "", text)).strip().lower()


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
    if not grouped:
        return []
    return [
        "공개 자료로 직접 근거를 확인하지 못한 항목은 아래와 같다. "
        "이는 부정적 판정이 아니라 현재 공개 자료로 확정할 수 있는 범위의 한계이다."
    ] + [
        f"- {technology} · {labels.get(perspective, perspective)}: {', '.join(dict.fromkeys(items))}"
        for (technology, perspective), items in grouped.items()
    ]


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
        lines.append(f"- {technology}: 추정 TRL {low}~{high} ({basis}). 기준별 판정이 달라 범위로 제시한다.")
    return lines


# 독자에게 의미 없는 내부 처리 메모. 평가 한계 목록에서 제외한다.
INTERNAL_NOTES = (
    "근거 ID 또는 평가 항목 검증 실패",
    "낮은 등급의 웹 자료만으로 작성된 Fact를 제외함",
    "개별 기술의 직접 근거가 없는 제품화·도입 주장을 제외함",
)
TRL_NOTE = "공개 정보 기반 추정 TRL이며 공식 인증값이 아님."
SECTION_BY_PERSPECTIVE = {"trl": "4.1", "market": "4.2", "stakeholder": "4.3", "domain": "4.4"}
MAX_LIMITATIONS = 2
MAX_CONFLICTS = 3


def _limitation_text(text):
    text = text.replace(TRL_NOTE, "").strip()
    if not text or any(note in text for note in INTERNAL_NOTES) or text.startswith("TRL"):
        return ""
    return text


def _page_markers(pages):
    """[1, 8, 9, 10] → '1·8–10'. 같은 문헌의 여러 페이지를 한 번에 표기한다."""
    pages = sorted(set(pages))
    groups, start = [], pages[0]
    for prev, cur in zip(pages, pages[1:] + [None]):
        if cur != prev + 1 if cur is not None else True:
            groups.append(str(start) if start == prev else f"{start}–{prev}")
            start = cur
    return "·".join(groups)


EVIDENCE_ID = r"(?:[\w-]+-p\d+-c\d+-[0-9a-f]{8}-[0-9a-f]{10}|web-[0-9a-f]{16}-[0-9a-f]{10}|counter-[0-9a-f]{16})"


def _strip_evidence_ids(text):
    """LLM이 본문에 그대로 옮긴 내부 근거 ID를 지운다. 인용은 ⟦CITE⟧ 표기로만 한다."""
    text = re.sub(rf"\s*\(\s*{EVIDENCE_ID}(?:\s*,\s*{EVIDENCE_ID})*\s*\)", "", text)
    return re.sub(EVIDENCE_ID, "", text)


def _render_report(state, draft, *, mode="live"):
    evidence = all_evidence(state)
    technologies = technology_names(state)
    used = set()

    def cited(paragraph):
        if not paragraph["text"].strip() or not valid_ids(paragraph["evidence_ids"], evidence):
            return ""
        ids = list(dict.fromkeys(paragraph["evidence_ids"]))
        used.update(ids)
        return _strip_evidence_ids(paragraph["text"]).strip() + " ⟦CITE:" + "|".join(ids) + "⟧"

    supplemental = {section_id: [] for section_id in SECTIONS}
    section_limitations = {section_id: [] for section_id in SECTIONS}
    fallback = synthesis_fallback(state)
    for index, selection in enumerate(state["technologies"], 1):
        supplemental[f"2.{index}"].append(
            f"- {selection['name']} ({selection['category']}): {selection['key_approach']} - "
            f"{selection['selection_reason']}"
        )
    ecosystem = {section_id: [] for section_id in SECTION_BY_PERSPECTIVE.values()}
    for perspective, section_id in SECTION_BY_PERSPECTIVE.items():
        analysis = state[f"{perspective}_analysis"]
        limitations = []
        for finding in analysis.get("findings", []):
            kind = "" if finding["kind"] == "Fact" else f" ({finding['kind']})"
            if finding.get("trl_level") is not None:
                kind += f" (판정 TRL {finding['trl_level']})"
            speakers = []
            if perspective == "stakeholder":
                for eid in finding["evidence_ids"]:
                    item = evidence.get(eid, {})
                    if item.get("speaker") or item.get("affiliation"):
                        speakers.append(
                            " / ".join(x for x in (item.get("speaker"), item.get("affiliation")) if x)
                        )
            speaker = f" (발언: {', '.join(dict.fromkeys(speakers))})" if speakers else ""
            if finding.get("evidence_scope") == "ecosystem":
                # 상위 시장·생태계 자료는 두 기술 공통 맥락이므로 기술 이름을 붙이지 않는다.
                text = cited(
                    {"text": f"[{finding['criterion']}] {finding['claim']}{kind}{speaker}", "evidence_ids": finding["evidence_ids"]}
                )
                if text:
                    ecosystem[section_id].append("- " + text)
                continue
            scope = " (비교 기술 근거)" if finding.get("evidence_scope") == "comparison" else ""
            label = f"[{finding['criterion']}] {finding['technology']}: {finding['claim']}{kind}{scope}{speaker}"
            text = cited({"text": label, "evidence_ids": finding["evidence_ids"]})
            if text:
                supplemental[section_id].append("- " + text)
                if limitation := _limitation_text(finding.get("limitation", "")):
                    limitations.append(f"- 평가 한계({finding['technology']}): {limitation}")
        limitations.extend(
            f"- 평가 한계: {text}" for x in analysis.get("limitations", []) if (text := _limitation_text(x))
        )
        if ecosystem[section_id]:
            supplemental[section_id].append(
                "상위 시장·생태계 맥락 (두 기술 공통, 개별 기술의 제품화·도입 근거 아님):"
            )
            supplemental[section_id].extend(_unique_lines(ecosystem[section_id]))
        section_limitations[section_id].extend(_unique_lines(limitations, limit=MAX_LIMITATIONS))

    for section_id, limitations in section_limitations.items():
        supplemental[section_id].extend(limitations)

    technical_items = set()
    # 3장 항목별 bullet은 서술 문단과 내용이 겹치므로, 서술이 없을 때만 대체 내용으로 쓴다.
    technical_bullets = {"3.1": [], "3.2": []}
    for eid, item in state["technical_evidence"].items():
        if item["technology"] not in technologies:
            continue
        section_id = "3.1" if item["technology"] == technologies[0] else "3.2"
        item_key = (item["technology"], item.get("item"))
        if item_key in technical_items or item.get("item") == "출처":
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
            technical_bullets[section_id].append("- " + text)

    # 1.1 문제 정의를 LLM이 채우지 못했을 때 쓸 대체 내용: 기술별 KV Cache 저장 위치 근거.
    problem_background = []
    for technology in technologies:
        item = next(
            (
                (eid, x)
                for eid, x in state["technical_evidence"].items()
                if x["technology"] == technology and x.get("item") == "KV Cache 저장 위치"
            ),
            None,
        )
        if item and (text := cited({"text": f"{technology}: {item[1]['claim']}", "evidence_ids": [item[0]]})):
            problem_background.append(text)

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

    for conflict in state["conflicts"][:MAX_CONFLICTS]:
        text = cited({"text": conflict["description"], "evidence_ids": conflict["evidence_ids"]})
        if text:
            supplemental["5.2"].append(f"- {conflict['category']}: {text}")
            if conflict.get("conditions"):
                supplemental["5.2"].append("  - 조건: " + conflict["conditions"])
            supplemental["5.2"].append("  - 해석: " + conflict["implication"])

    lines = ["# ITME와 CXL-PIM 데이터센터 적용성 비교 평가", ""]
    if mode != "live":
        lines.extend(["> DEMO / 테스트용 가상 근거입니다. 기술 평가나 제출 보고서로 사용하지 마세요.", ""])
    lines.extend(["## SUMMARY", ""])
    # SUMMARY 길이는 글자 기준 700자 이내; 실제 반 페이지 여부는 편집 단계에서 확인.
    length = 0
    for paragraph in state["synthesis"].get("summary", []):
        if length + len(paragraph["text"]) > 900:
            continue
        text = cited(paragraph)
        if text:
            lines.append("- " + text)
            length += len(paragraph["text"])
    if length:
        lines.append("")
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
    printed = set()
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
        if section_id in SECTION_BY_PERSPECTIVE.values():
            # 관점별 finding이 아래에 목록으로 나오므로, LLM 서술은 도입 문단 하나만 쓴다.
            paragraphs = paragraphs[:1]
        if section_id == "1.1" and not paragraphs:
            paragraphs = problem_background
        elif section_id == "2.3" and not paragraphs:
            first, second = state["technologies"][:2]
            paragraphs = [
                f"두 기술은 모두 CXL로 KV Cache의 메모리 한계를 다루지만, {first['name']}는 "
                f"{first['key_approach']}, {second['name']}은 {second['key_approach']}으로 접근 방식이 달라 "
                "저장 위치·연산 위치·데이터 이동 경로의 차이를 비교하기 위해 선정했다."
            ]
        if section_id == "1.2":
            paragraphs.insert(
                0,
                f"분석 범위는 {state['domain']} 환경에서 ITME와 CXL-PIM의 KV Cache 관리 방식이다. "
                "두 기술을 기술 성숙도, 시장성, 이해관계자, 데이터센터·클라우드 적용성의 4개 관점에서 "
                "공개 논문과 웹 자료로 평가하며, 기술 간 우열이 아니라 관점별 특징과 한계를 정리한다.",
            )
        elif section_id in ("3.1", "3.2") and not paragraphs:
            paragraphs = technical_bullets[section_id]
        elif section_id == "4.1":
            paragraphs = trl_summary(state, technologies) + paragraphs
            paragraphs.append("TRL은 공개 정보 기반 추정이며 공식 인증값이 아니다.")
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
            reworks = sum(state["rework_counts"].get(p, 0) for p in PERSPECTIVES)
            paragraphs.append(
                f"기술 재검색 {state['retry_count']}회, 관점별 재조사 {reworks}회, 주요 주장별 반대 근거 검색 "
                f"{len(state['counter_evidence'])}회, 반대 근거 채택 {found}건. "
                "반대 근거 미발견은 원 주장의 참을 입증하지 않는다. "
                "인용문 일치는 자동 확인했으나 주장과 인용의 의미적 일치에는 사람의 검토가 필요하다."
            )
        paragraphs.extend(supplemental[section_id])
        # 같은 문단이 여러 절에 반복되면 처음 나온 절에만 남긴다(목록 표지·하위 줄은 제외).
        paragraphs = [
            p for p in _unique_lines(paragraphs)
            if p.startswith(("- ", "  - ")) or _normalize(p) not in printed
        ]
        printed.update(_normalize(p) for p in paragraphs)
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
            (
                evidence[eid].get("source_tier", 5 if evidence[eid].get("role") == "web" else 1)
                for eid in ref["evidence_ids"]
                if eid in evidence
            ),
            default=5,
        )
        lines.append(
            _reference_text(index, ref) + f" [출처 등급 {tier}: {SOURCE_TIER_LABELS[tier]}]"
        )

    def replace_citation(match):
        pages = {}
        for eid in match.group(1).split("|"):
            pages.setdefault(by_eid[eid], [])
            if evidence[eid].get("page") is not None:
                pages[by_eid[eid]].append(evidence[eid]["page"])
        markers = []
        for index, numbers in pages.items():
            if not numbers:
                markers.append(str(index))
            else:
                prefix = "p." if len(set(numbers)) == 1 else "pp."
                markers.append(f"{index}, {prefix}{_page_markers(numbers)}")
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
        "analyses": {
            p: {
                **state[f"{p}_analysis"],
                "limitations": [
                    t for x in state[f"{p}_analysis"].get("limitations", []) if (t := _limitation_text(x))
                ],
            }
            for p in PERSPECTIVES
        },
        "conflicts": state["conflicts"],
        "counter_evidence": state["counter_evidence"],
        "missing_evidence": state["missing_evidence"],
    }
    directive = state.get("directive") or {}
    if directive.get("target") == "report" and directive.get("missing_items"):
        # 품질 평가 미달로 Supervisor가 재작성을 지시한 경우, 실패 항목과 사유를 함께 전달한다.
        payload["quality_feedback"] = directive["missing_items"]
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
