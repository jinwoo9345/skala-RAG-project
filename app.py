"""CLI: 그래프 확인, 오프라인 demo, RAG 인덱스, 실제 보고서 생성."""

import argparse
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from config import MAX_RETRIES, OUTPUT_DIR, PROJECT_ROOT, Settings
from state import initial_state
from workflow_logging import ACTIVE_RUN, configure_logging, error_location, get_logger


def preflight(settings):
    modules = (
        "langgraph",
        "langchain",
        "langchain_openai",
        "langchain_teddynote",
        "pydantic",
        "openai",
        "httpx",
        "pymupdf",
        "reportlab",
        "numpy",
        "sentence_transformers",
        "rank_bm25",
    )
    checks = {name: importlib.util.find_spec(name) is not None for name in modules}
    keys = {name: bool(os.getenv(name)) for name in ("OPENAI_API_KEY", "TAVILY_API_KEY")}
    from rag.pipeline import _manifest_rows

    papers = _manifest_rows(settings.manifest)
    return {
        "dependencies": checks,
        "credentials_present": keys,
        "pdf_count": len(papers),
        "ready": all(checks.values()) and all(keys.values()),
    }


def save_outputs(result, settings, run_id, output_dir, mode):
    from dataclasses import asdict

    from tools.pdf_writer import render_pdf

    directory = output_dir / (datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S") + "-" + run_id)
    directory.mkdir(parents=True, exist_ok=False)
    render_pdf(result["final_report"], directory / "report.pdf")
    (directory / "state.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": mode,
                "settings": asdict(settings),
                "created_at": datetime.now().astimezone().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description="ITME/CXL-PIM Agentic RAG 평가 보고서")
    parser.add_argument("--show-state", action="store_true")
    parser.add_argument("--show-graph", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true", help="실제 API 호출; 비용 발생")
    modes.add_argument("--demo", action="store_true", help="외부 API 없는 가상 자료 실행")
    modes.add_argument("--index", action="store_true", help="PDF 임베딩 인덱스 준비")
    modes.add_argument("--check", action="store_true", help="설정·의존성·PDF 존재 확인 (외부 호출 없음)")
    parser.add_argument(
        "--scenario",
        choices=["normal", "retry_success", "retry_exhausted", "second_missing", "counter_found"],
        default="normal",
    )
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--max-retries", type=int, default=MAX_RETRIES)
    parser.add_argument("--env-file", type=Path, help="명시한 dotenv 파일 사용; dotenv 값 우선")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    run_id = uuid4().hex[:8]
    log_file = args.log_file
    if log_file is None and (args.run or args.demo or args.index):
        log_file = args.output_dir / "logs" / f"{datetime.now(UTC).astimezone():%Y%m%d-%H%M%S}-{run_id}.log"
    configure_logging(args.log_level, log_file)
    logger = get_logger(run_id)
    token = ACTIVE_RUN.set(run_id)
    started = perf_counter()
    try:
        from dotenv import load_dotenv

        if args.env_file and not args.env_file.is_file():
            raise FileNotFoundError("지정한 env 파일 없음")
        load_dotenv(args.env_file or PROJECT_ROOT / ".env", override=True)
        settings = Settings.from_env()
        state = initial_state(max_retries=args.max_retries)
        if args.show_state:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        if args.check:
            checks = preflight(settings)
            print(json.dumps(checks, ensure_ascii=False, indent=2))
            return 0 if checks["ready"] else 1
        if args.index:
            from rag.pipeline import get_retriever

            retriever = get_retriever(settings, rebuild=args.rebuild_index)
            logger.info("INDEX_READY | chunks=%d", len(retriever.chunks))
        if args.show_graph:
            from graph import build_graph

            print(build_graph(run_id).get_graph().draw_mermaid())
        if args.run or args.demo:
            logger.info("RUN_START | mode=%s | retries=%d", "demo" if args.demo else "live", args.max_retries)
            from graph import build_graph

            if args.demo:
                from demo import demo_services

                services = demo_services(args.scenario, settings)
                logger.warning("DEMO_MODE | 가상 자료; 실제 기술 평가 아님")
            else:
                from services import live_services

                services = live_services(settings)
            graph = build_graph(run_id, services)
            result = graph.invoke(
                state, config={"recursion_limit": 30 + 5 * args.max_retries, "max_concurrency": 4}
            )
            directory = save_outputs(result, settings, run_id, args.output_dir, services.mode)
            logger.info("REPORT_SAVED | directory=%s", directory)
            logger.info(
                "RUN_DONE | elapsed=%.3fs | chars=%d | missing=%d",
                perf_counter() - started,
                len(result["final_report"]),
                len(result["missing_evidence"]),
            )
            print(directory / "report.pdf")
        if not any((args.show_state, args.show_graph, args.run, args.demo, args.index, args.check)):
            parser.print_help()
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI 경계에서 민감한 API 오류 본문을 노출하지 않음
        # 외부 API exception 본문에는 민감 입력이 포함될 수 있어 그대로 기록하지 않는다.
        logger.error(
            "RUN_FAILED | elapsed=%.3fs | type=%s | location=%s",
            perf_counter() - started,
            type(exc).__name__,
            error_location(exc),
        )
        logger.error("실행 설정은 --check로 확인; OP_FAILED/NODE_FAILED에서 실패 단계 확인")
        return 1
    finally:
        ACTIVE_RUN.reset(token)


if __name__ == "__main__":
    raise SystemExit(main())
