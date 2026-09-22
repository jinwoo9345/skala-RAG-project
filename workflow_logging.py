"""State 본문 대신 단계, 데이터 개수, 처리 시간을 기록한다."""

import logging
import traceback
from collections.abc import Mapping
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from pathlib import Path
from time import perf_counter

LOGGER_NAME = "capstone.workflow"
ACTIVE_RUN = ContextVar("capstone_run", default="standalone")
ACTIVE_NODE = ContextVar("capstone_node", default="direct")


def error_location(exc: Exception) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    frame = frames[-1]
    return f"{Path(frame.filename).name}:{frame.lineno} ({frame.name})"


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def get_logger(run_id: str | None = None) -> logging.LoggerAdapter:
    class RunLogger(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            return f"[run={self.extra['run_id'] or ACTIVE_RUN.get()}] {msg}", kwargs

    return RunLogger(logging.getLogger(LOGGER_NAME), {"run_id": run_id})


def summarize_state(value) -> str:
    """문서·프롬프트·응답 원문은 출력하지 않고 필드별 크기만 표시."""
    if not isinstance(value, Mapping):
        return type(value).__name__
    parts = []
    for key, item in value.items():
        if isinstance(item, (list, dict, tuple, set)):
            detail = f"{len(item)}개"
        elif isinstance(item, str):
            detail = f"{len(item)}자"
        elif key in ("max_retries", "retry_count") and isinstance(item, int):
            detail = str(item)
        else:
            detail = type(item).__name__
        parts.append(f"{key}={detail}")
    return ", ".join(parts) or "변경 없음"


def log_node(name, function, logger):
    @wraps(function)
    def wrapped(state, *args, **kwargs):
        run_token = ACTIVE_RUN.set(logger.extra["run_id"] or ACTIVE_RUN.get())
        node_token = ACTIVE_NODE.set(name)
        started = perf_counter()
        logger.info("NODE_START | %s", name)
        logger.debug("NODE_INPUT | %s | %s", name, summarize_state(state))
        if name == "query_rewrite":
            logger.info(
                "RETRY | 완료 횟수=%s | 한도=%s | 부족 근거=%d개",
                state.get("retry_count"),
                state.get("max_retries"),
                len(state.get("missing_evidence", [])),
            )
        elif name == "fan_out":
            logger.info("FAN_OUT | TRL·시장·이해관계자·도메인 평가 시작")
        elif name == "fan_in":
            logger.info("FAN_IN | 4개 평가 완료; 근거·참고문헌 병합")
        try:
            result = function(state, *args, **kwargs)
        except Exception as exc:
            # API 오류에 요청 원문 등이 포함될 수 있어 메시지 대신 유형만 기록.
            logger.error(
                "NODE_FAILED | %s | %.3fs | %s%s | 위치=%s",
                name,
                perf_counter() - started,
                type(exc).__name__,
                " (미구현 단계)" if isinstance(exc, NotImplementedError) else "",
                error_location(exc),
            )
            raise
        finally:
            ACTIVE_NODE.reset(node_token)
            ACTIVE_RUN.reset(run_token)
        logger.info(
            "NODE_DONE | %s | %.3fs | 업데이트: %s",
            name,
            perf_counter() - started,
            summarize_state(result),
        )
        if name in ("first_check", "second_check"):
            missing = result.get("missing_evidence", state.get("missing_evidence", []))
            logger.info("EVIDENCE_RESULT | %s | 부족 근거=%d개", name, len(missing))
        if name == "report":
            logger.info("REPORT_READY | 보고서=%d자", len(result.get("final_report", "")))
        return result

    return wrapped


def log_router(function, logger):
    @wraps(function)
    def wrapped(state, *args, **kwargs):
        logger.info(
            "ROUTE_START | 부족 근거=%d개 | 재검색=%s/%s",
            len(state.get("missing_evidence", [])),
            state.get("retry_count"),
            state.get("max_retries"),
        )
        try:
            route = function(state, *args, **kwargs)
        except Exception as exc:
            logger.error("ROUTE_FAILED | %s | 위치=%s", type(exc).__name__, error_location(exc))
            raise
        destinations = {
            "rewrite": "Query Rewrite → 기술 조사 재검색",
            "evaluate": "4개 관점 평가",
            "retry": "재검색 한도 검사",
            "missing": "부족 근거 확정 기록",
            "verify": "반대 근거 검증",
        }
        logger.info("ROUTE_SELECTED | %s → %s", route, destinations.get(route, "미정의 경로"))
        if route == "evaluate" and state.get("missing_evidence"):
            logger.warning("EVIDENCE_GAP | 부족 근거를 유지한 채 평가 단계로 진행")
        return route

    return wrapped


def log_operation(name: str):
    """RAG·도구의 동기 함수에 적용. 입력 값은 길이/개수만 기록한다."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            logger = get_logger()
            started = perf_counter()
            bound = signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            logger.info(
                "OP_START | node=%s | %s | 입력: %s",
                ACTIVE_NODE.get(),
                name,
                summarize_state(bound.arguments),
            )
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                logger.error(
                    "OP_FAILED | node=%s | %s | %.3fs | %s | 위치=%s",
                    ACTIVE_NODE.get(),
                    name,
                    perf_counter() - started,
                    type(exc).__name__,
                    error_location(exc),
                )
                raise
            logger.info(
                "OP_DONE | node=%s | %s | %.3fs | 결과: %s",
                ACTIVE_NODE.get(),
                name,
                perf_counter() - started,
                summarize_state({"result": result}),
            )
            return result

        return wrapped

    return decorate
