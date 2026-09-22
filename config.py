"""설계서에 명시된 고정 설정. 기술 선정은 Human 기반이다."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

TECHNOLOGIES = ["ITME", "CXL-PIM"]
SUPPORTING_TECHNOLOGIES = ("InfiniGen", "PagedAttention", "Mooncake", "CENT", "CacheGen")
DOMAIN = "데이터센터·클라우드"
EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
EMBEDDING_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"
MAX_DOCUMENT_PAGES = 200
MAX_RETRIES = 2
DOCUMENT_MANIFEST = PROJECT_ROOT / "data" / "documents.json"
INDEX_DIR = PROJECT_ROOT / "data" / "index"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

TECHNICAL_EVIDENCE_ITEMS = (
    "작동 원리",
    "KV Cache 저장 위치",
    "데이터 이동 방식",
    "실험 환경",
    "주요 성능 결과",
    "비교 Baseline",
    "한계점",
    "출처",
)
DOMAIN_CRITERIA = (
    "용량",
    "성능",
    "데이터 이동",
    "확장성",
    "비용과 구축 복잡도",
)

PERSPECTIVES = ("trl", "market", "stakeholder", "domain")
EVALUATION_CRITERIA = {
    "trl": ("논문·PoC·Prototype", "실환경 검증", "상용 제품 여부"),
    "market": ("제품화", "실제 도입", "지원 생태계", "시장 규모·성장성", "도입 장벽"),
    "stakeholder": ("경쟁 진영", "도입 기업·개발자", "투자 업계"),
    "domain": DOMAIN_CRITERIA,
}

EVALUATION_GUIDANCE = {
    "trl": "논문, PoC, Prototype, 실환경 검증, 상용 제품을 구분하고 공개 정보 기반 추정 TRL로 표시한다.",
    "market": "개별 기술의 제품화·채택 근거와 상위 CXL/PIM 시장 규모·성장 자료를 명시적으로 구분한다.",
    "stakeholder": "기대 효과, 비용 부담, 호환성, 도입 난이도, 운영 우려를 확인하고 실제 발언과 Agent 해석을 구분한다.",
    "domain": (
        "용량은 KV Cache 용량·HBM 절감, 성능은 Latency·Throughput과 TTFT·TPOT·요청 수, "
        "데이터 이동은 이동량·대역폭 병목·Attention 실행 위치, 확장성은 Context 길이·동시 요청 수, "
        "비용과 구축 복잡도는 추가 HW·호환성·소프트웨어 변경·운영 부담을 확인한다."
    ),
}


# 출처 우선순위. 앞 단계일수록 신뢰도가 높으며, 검색 결과를 이 순서로 정렬한다.
# 어느 단계에도 없는 도메인은 2차 자료로 보고 순위를 뒤로 미룬다.
SOURCE_PRIORITY = (
    # 1. 논문
    (
        "arxiv.org", "ar5iv.labs.arxiv.org", "dl.acm.org", "acm.org",
        "ieeexplore.ieee.org", "ieee.org", "usenix.org", "openreview.net",
    ),
    # 2. 표준·공식 기술문서
    (
        "computeexpresslink.org", "nvidia.com", "developer.nvidia.com",
        "intel.com", "amd.com", "samsung.com", "semiconductor.samsung.com",
        "skhynix.com", "news.skhynix.com",
    ),
    # 3. 특허
    ("patents.google.com", "uspto.gov", "patentscope.wipo.int", "wipo.int"),
    # 4. 시장·제품 정보 (기업 공시·애널리스트)
    ("sec.gov", "gartner.com", "idc.com"),
)
TRUSTED_DOMAINS = tuple(domain for tier in SOURCE_PRIORITY for domain in tier)

# 개인 블로그·SNS·커뮤니티는 Opinion 근거로도 사용하지 않는다.
EXCLUDED_WEB_DOMAINS = (
    "tistory.com",
    "blog.naver.com",
    "cafe.naver.com",
    "brunch.co.kr",
    "velog.io",
    "medium.com",
    "linkedin.com",
    "reddit.com",
    "quora.com",
)


def source_tier(url: str) -> int:
    """URL의 출처 등급(1~4)을 돌려준다. 목록에 없으면 5(2차 자료)."""
    host = url.split("//")[-1].split("/")[0].split("?")[0].lower()
    for index, tier in enumerate(SOURCE_PRIORITY, 1):
        if any(host == domain or host.endswith("." + domain) for domain in tier):
            return index
    return 5


# 기술 × 평가 기준 단위 검색어. 기준마다 별도 질의를 던져 자료 편중을 막는다.
QUERY_TERMS = {
    "검증 단계": "proof of concept prototype validation stage",
    "실환경 검증": "production deployment operational validation",
    "제품화 근거": "commercial product roadmap availability",
    "제품화": "commercial product availability launch",
    "실제 도입": "customer deployment adoption case",
    "지원 생태계": "software ecosystem toolchain support",
    "시장 규모·성장성": "market size growth forecast",
    "도입 장벽": "adoption barrier integration cost",
    "기대 효과": "expected benefit developer operator statement",
    "비용 부담": "cost burden capex opex concern",
    "호환성": "compatibility interoperability integration",
    "도입 난이도": "deployment migration difficulty",
    "운영상 우려": "operations reliability maintenance concern",
    "용량": "KV cache capacity HBM reduction",
    "성능": "latency TTFT TPOT throughput baseline",
    "데이터 이동": "bandwidth data movement attention placement",
    "확장성": "context length concurrent request scalability",
    "비용과 구축 복잡도": "hardware cost integration complexity",
}

# 대상 기술을 고유하게 지칭하는 명칭. 약어만으로는 동명이의 자료가 섞인다.
TECH_PROFILES = {
    "ITME": {
        "paper_ids": ("2606.12556",),
        "distinctive": ("Inference Tiered Memory Expansion", "Disaggregated CXL-Hybrid"),
        "ambiguous": ("ITME",),
    },
    "CXL-PIM": {
        "paper_ids": ("2511.00321",),
        "distinctive": ("Scalable Processing-Near-Memory", "PNM-KV", "1M-Token LLM Inference"),
        "ambiguous": ("CXL-PIM", "CXL-PNM", "CXL PNM"),
    },
}

# 출처 등급별 허용 용도. 저품질 출처를 제거하지는 않되 Fact 근거로는 쓰지 않는다.
#   1~2 논문·공식 문서 : 기술/제품 Fact 가능
#   3~4 특허·시장 자료 : 시장 전망 Fact 가능
#   5   블로그·SNS 등  : Opinion 전용
FACT_MAX_TIER = 4
TECHNICAL_FACT_MAX_TIER = 2


@dataclass(frozen=True)
class Settings:
    """설계서 미지정 파라미터는 교체 가능하며 최적값으로 간주하지 않는다."""

    model: str = "gpt-4.1-mini"
    judge_model: str = "gpt-4.1-mini"
    embedding_model: str = EMBEDDING_MODEL
    embedding_revision: str | None = EMBEDDING_REVISION
    chunk_tokens: int = 400
    overlap_tokens: int = 50
    dense_k: int = 10
    bm25_k: int = 10
    top_k: int = 10
    max_context_chunks: int = 30
    search_results: int = 5
    max_claims_per_perspective: int = 2
    request_timeout: float = 60.0
    manifest: Path = DOCUMENT_MANIFEST
    index_dir: Path = INDEX_DIR
    model_cache_dir: Path = PROJECT_ROOT / ".cache" / "models"

    def __post_init__(self):
        if not 0 <= self.overlap_tokens < self.chunk_tokens <= 500:
            raise ValueError("0 <= overlap_tokens < chunk_tokens <= 500 필요")
        if (
            min(
                self.dense_k,
                self.bm25_k,
                self.top_k,
                self.max_context_chunks,
                self.search_results,
                self.max_claims_per_perspective,
            )
            < 1
            or self.request_timeout <= 0
        ):
            raise ValueError("검색/문맥/주장 개수 및 timeout은 양수여야 함")

    @classmethod
    def from_env(cls):
        model = os.getenv("GENERATOR_MODEL") or "gpt-4.1-mini"
        embedding_model = os.getenv("EMBEDDING_MODEL") or EMBEDDING_MODEL
        return cls(
            model=model,
            judge_model=os.getenv("JUDGE_MODEL") or model,
            embedding_model=embedding_model,
            embedding_revision=os.getenv("EMBEDDING_REVISION")
            or (EMBEDDING_REVISION if embedding_model == EMBEDDING_MODEL else None),
            chunk_tokens=int(os.getenv("CHUNK_TOKENS", "400")),
            overlap_tokens=int(os.getenv("CHUNK_OVERLAP", "50")),
            dense_k=int(os.getenv("DENSE_TOP_K", "10")),
            bm25_k=int(os.getenv("BM25_TOP_K", "10")),
            top_k=int(os.getenv("TOP_K", "10")),
            max_context_chunks=int(os.getenv("MAX_CONTEXT_CHUNKS", "30")),
            search_results=int(os.getenv("SEARCH_RESULTS", "5")),
            max_claims_per_perspective=int(os.getenv("COUNTER_CLAIMS_PER_PERSPECTIVE", "2")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "60")),
        )
