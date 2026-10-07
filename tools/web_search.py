"""수업 코드의 TavilySearch와 근거 State 사이의 최소 어댑터."""

import hashlib
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from langchain_teddynote.tools.tavily import TavilySearch

from config import EXCLUDED_WEB_DOMAINS, TECH_PROFILES, source_tier
from workflow_logging import get_logger, log_operation


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.username:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def _latin_ratio(text: str) -> float:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0.0
    return sum("a" <= character.lower() <= "z" for character in letters) / len(letters)


def source_title(row: dict, url: str, content: str) -> str:
    """영문 URL에 다른 문자권 제목이 붙으면 본문 제목 또는 URL slug로 교정한다.

    Tavily가 다국어 사이트의 기본 언어 제목을 반환하는 경우가 있다. 추가 HTTP 호출 없이
    이미 받은 raw_content의 첫 부분에서 URL slug와 가장 가까운 영문 제목을 찾는다.
    """
    title = re.sub(r"\s+", " ", (row.get("title") or "").strip())
    path_parts = [part for part in urlsplit(url).path.split("/") if part]
    if not path_parts or path_parts[0].lower() != "en" or _latin_ratio(title) >= 0.6:
        return title or url

    slug = path_parts[-1].removesuffix(".html")
    slug_words = set(re.findall(r"[a-z0-9]+", slug.lower()))
    candidates = []
    for raw_line in content.splitlines()[:40]:
        line = re.sub(r"^[#>*\-\s]+|\s+", " ", raw_line).strip()
        if not 8 <= len(line) <= 180 or _latin_ratio(line) < 0.6:
            continue
        words = set(re.findall(r"[a-z0-9]+", line.lower()))
        score = len(slug_words & words) / max(len(slug_words), 1)
        candidates.append((score, -len(line), line))
    if candidates and max(candidates)[0] >= 0.6:
        return max(candidates)[2]
    return slug.replace("-", " ").strip() or title or url


def blog_url(url: str) -> bool:
    """블로그 글인지 확인한다: /blog·/blogs(·*_blogs_N) 경로, blog. 서브도메인."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host.startswith(("blog.", "blogs.")) or bool(
        re.search(r"(^|/)[\w-]*blogs?([_-]\w*)?(/|$)", parts.path.lower())
    )


def published_day(value: str) -> str:
    """Tavily 발행일(RFC 2822 또는 ISO)을 YYYY-MM-DD로 바꾼다. 해석할 수 없으면 빈 문자열."""
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        match = re.match(r"\d{4}-\d{2}-\d{2}", value)
        return match.group(0) if match else ""


def excluded_web_domain(url: str) -> bool:
    """개인 블로그·SNS·커뮤니티 URL인지 확인한다."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in EXCLUDED_WEB_DOMAINS)


# 고유 명칭은 제목과 본문 앞부분에서만 찾는다. 페이지 하단의 관련 글 목록에
# 이름만 나온 다른 기술 자료를 대상 기술의 직접 근거로 오인하지 않기 위해서다.
DIRECT_SCOPE_CHARS = 2000

CORE_PAPER_IDS = tuple(pid for profile in TECH_PROFILES.values() for pid in profile["paper_ids"])


def core_paper_url(url: str) -> bool:
    """평가 대상 핵심 논문 자체의 URL인지 확인한다. 논문 내용은 RAG로 이미 사용한다."""
    return any(pid in url for pid in CORE_PAPER_IDS)


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
    head = text[:DIRECT_SCOPE_CHARS].lower()
    if any(name.lower() in head for name in profile["distinctive"]):
        return "direct"
    if any(
        re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in profile["ambiguous"]
    ) or re.search(
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
        published = published_day(row.get("published_date") or "")
        # 발행일을 확인할 수 있는 자료만 쓰고, 블로그 글은 제외한다.
        if not published or blog_url(url):
            continue
        content = row.get("raw_content") or row.get("content") or ""
        # LLM은 인용 시 마크다운 기호를 지우므로, 원문에서도 미리 지워 인용문 대조를 맞춘다.
        content = re.sub(r"\*\*|__|^#+\s*", "", content, flags=re.MULTILINE)
        if (
            not url
            or excluded_web_domain(url)
            or core_paper_url(url)
            or not content.strip()
            or url in seen
        ):
            continue
        seen.add(url)
        sources.append(
            {
                "chunk_id": "web-" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "document_id": url,
                "source": source_title(row, url, content),
                "source_url": url,
                "page": None,
                "role": "web",
                "content": content[:12000],
                "content_type": "full_text" if row.get("raw_content") else "snippet",
                "published_date": published,
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
    # Tavily는 topic="news"일 때만 결과별 발행일(published_date)을 제공한다.
    results = tool.search(
        query=query,
        topic="news",
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
