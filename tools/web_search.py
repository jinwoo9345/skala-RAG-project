"""수업 코드의 TavilySearch와 근거 State 사이의 최소 어댑터."""

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from langchain_teddynote.tools.tavily import TavilySearch

from config import EXCLUDED_WEB_DOMAINS, TECH_PROFILES, source_tier
from workflow_logging import get_logger, log_operation


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.username:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def excluded_web_domain(url: str) -> bool:
    """개인 블로그·SNS·커뮤니티 URL인지 확인한다."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in EXCLUDED_WEB_DOMAINS)


def relevance(text: str, technology: str) -> str | None:
    """근거 범위를 direct / ecosystem / comparison 으로 나눈다.

    고유 명칭이 있으면 대상 기술 자체(direct), 약어나 일반 CXL/PIM 용어만
    있으면 상위 시장(ecosystem), 별도 시스템이면 comparison. 어느 쪽도
    아니면 None을 돌려 근거로 쓰지 않는다.
    """
    profile = TECH_PROFILES.get(technology)
    if profile is None:
        return "comparison"
    if re.search(r"\bCENT\b|PIM Is All You Need", text, re.IGNORECASE):
        return "comparison"
    if any(name.lower() in text.lower() for name in profile["distinctive"]):
        return "direct"
    if any(name.lower() in text.lower() for name in profile["ambiguous"]) or re.search(
        r"\b(CXL|PIM|PNM|KV[- ]?cache)\b", text, re.IGNORECASE
    ):
        return "ecosystem"
    return None


def create_web_search():
    """03-WebSearch.ipynb와 같은 TavilySearch 도구를 생성한다."""
    return TavilySearch()


def normalize_web_results(results: list[dict]) -> list[dict]:
    """수업 도구 결과에 Evidence가 요구하는 ID·출처 필드만 보충한다."""
    sources, seen = [], set()
    for row in results:
        url = canonical_url(row.get("url", ""))
        content = row.get("raw_content") or row.get("content") or ""
        if not url or excluded_web_domain(url) or not content.strip() or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "chunk_id": "web-" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "document_id": url,
                "source": row.get("title") or url,
                "source_url": url,
                "page": None,
                "role": "web",
                "content": content[:12000],
                "content_type": "full_text" if row.get("raw_content") else "snippet",
                "published_date": row.get("published_date") or "",
                "author": row.get("author") or "",
                "source_type": "web",
                "site_name": urlsplit(url).netloc,
                "source_tier": source_tier(url),
                "venue": "",
                "identifier": "",
            }
        )
    # 1.논문 > 2.공식 문서 > 3.특허 > 4.시장 자료 > 5.기타 순으로 사용한다.
    sources.sort(key=lambda item: item["source_tier"])
    return sources


@log_operation("WEB_SEARCH")
def web_search(tool, query: str, *, max_results: int | None = None) -> list[dict]:
    """수업 예제의 ``tavily_tool.search`` 호출을 그대로 사용한다."""
    results = tool.search(
        query=query,
        topic="general",
        max_results=max_results,
        format_output=False,
    )
    sources = normalize_web_results(results)
    get_logger().info(
        "WEB_RESULTS | sources=%d | tiers=%s",
        len(sources),
        sorted(x["source_tier"] for x in sources),
    )
    return sources


# 기존 프로젝트 호출부와의 호환성을 위한 별칭이다.
search_web = web_search


def summarize_web_sources(sources: list[dict]) -> dict:
    """Fact/Opinion 의미 분석은 각 평가 Agent가 수행한다."""
    return {"sources": sources, "source_count": len(sources)}
