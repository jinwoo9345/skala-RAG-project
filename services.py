"""실행별 의존성 주입. Graph/Agent 테스트는 외부 API 없이 fake를 주입한다."""

from dataclasses import dataclass
from typing import Any

from config import Settings


@dataclass
class Services:
    llm: Any
    retriever: Any
    web: Any
    settings: Settings
    mode: str = "live"
    question_rewriter: Any = None
    report_writer: Any = None


def live_services(settings=None):
    import os

    settings = settings or Settings.from_env()
    missing = [name for name in ("OPENAI_API_KEY", "TAVILY_API_KEY") if not os.getenv(name)]
    if missing:
        raise ValueError("설정 필요: " + ", ".join(missing))
    from rag.pipeline import get_retriever
    from tools.llm import OpenAILLM
    from tools.query_rewrite import create_question_rewriter
    from tools.report_writer import create_report_writer
    from tools.web_search import create_web_search

    return Services(
        OpenAILLM(settings),
        get_retriever(settings),
        create_web_search(),
        settings,
        question_rewriter=create_question_rewriter(settings.model),
        report_writer=create_report_writer(settings.model),
    )
