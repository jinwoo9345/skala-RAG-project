import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main
from config import EVALUATION_CRITERIA, PERSPECTIVES
from demo import DemoLLM, demo_services
from evidence import fan_in
from graph import build_graph
from state import initial_state
from workflow_logging import configure_logging


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        configure_logging("ERROR")

    def run_scenario(self, scenario="normal", max_retries=2):
        services = demo_services(scenario)
        result = build_graph("test", services).invoke(
            initial_state(max_retries=max_retries), {"recursion_limit": 100, "max_concurrency": 4}
        )
        return result, services

    def test_normal_full_run_preserves_16_fields(self):
        state, services = self.run_scenario()
        self.assertEqual(set(state), set(initial_state()))
        self.assertEqual(len(state), 16)
        self.assertEqual(len(state["search_queries"]), 16)
        self.assertFalse(state["missing_evidence"])
        self.assertEqual(state["retry_count"], 0)
        for perspective in PERSPECTIVES:
            technologies = {f["technology"] for f in state[f"{perspective}_analysis"]["findings"]}
            self.assertEqual(technologies, {"ITME", "CXL-PIM"})
        self.assertIn("DEMO / 테스트용", state["final_report"])
        self.assertIn("## REFERENCE", state["final_report"])
        self.assertNotIn("근거 검증 부록", state["final_report"])
        self.assertNotIn("CITE:", state["final_report"])
        self.assertNotIn("검증된 자료로 작성할 내용이 부족합니다", state["final_report"])
        self.assertNotIn("미확인:", state["final_report"])
        for heading in (
            "## SUMMARY",
            "## 1. 분석 배경 및 문제 정의",
            "## 2. 평가 대상 기술 선정",
            "## 3. 기술 개요",
            "## 4. 다관점 평가",
            "## 5. 관점 간 종합 및 시사점",
            "## 6. 분석 한계 및 신뢰성 확보",
            "### 4.4 데이터센터·클라우드 적용성",
        ):
            self.assertIn(heading, state["final_report"])
        # 기술 × 평가 기준 단위 검색: (TRL 3 + 시장 5 + 이해관계자 3) × 기술 2 = 22회.
        # 결과가 있으면 대체 질의를 쓰지 않는다. 여기에 반대 근거 검색 8회가 더해진다.
        web_criteria = sum(len(EVALUATION_CRITERIA[p]) for p in ("trl", "market", "stakeholder"))
        self.assertEqual(len(services.web.calls), web_criteria * 2 + 8)
        self.assertTrue(all(x["search_count"] == 1 for x in state["counter_evidence"].values()))

    def test_retry_success_clears_first_gaps(self):
        state, _ = self.run_scenario("retry_success")
        self.assertEqual(state["retry_count"], 1)
        self.assertEqual(state["missing_evidence"], [])
        self.assertTrue(state["final_report"])

    def test_retry_exhaustion_continues_and_preserves_gaps(self):
        state, _ = self.run_scenario("retry_exhausted")
        self.assertEqual(state["retry_count"], 2)
        gaps = [x for x in state["missing_evidence"] if x["stage"] == 1]
        self.assertEqual(len(gaps), 16)
        self.assertTrue(all(x["status"] == "retry_exhausted" for x in gaps))
        self.assertIn("ITME의 기술 조사에서 공개 직접 근거가 제한된 항목", state["final_report"])
        self.assertNotIn("검증된 자료로 작성할 내용이 부족합니다", state["final_report"])

    def test_zero_retries(self):
        state, services = self.run_scenario("retry_exhausted", max_retries=0)
        self.assertEqual(state["retry_count"], 0)
        self.assertEqual(services.llm.technical_calls, {"ITME": 1, "CXL-PIM": 1})

    def test_second_gaps_do_not_restart_retrieval(self):
        state, services = self.run_scenario("second_missing")
        self.assertEqual(state["retry_count"], 0)
        self.assertTrue(any(x["stage"] == 2 and x["status"] == "recorded" for x in state["missing_evidence"]))
        self.assertEqual(services.llm.technical_calls["ITME"], 1)
        self.assertTrue(state["final_report"])

    def test_counter_found_is_cited_and_saved_in_final_references(self):
        state, _ = self.run_scenario("counter_found")
        self.assertTrue(all(x["status"] == "found" for x in state["counter_evidence"].values()))
        self.assertIn("DEMO 가상 제약 사항", state["final_report"])
        self.assertTrue(any(e.startswith("counter-") for r in state["references"] for e in r["evidence_ids"]))

    def test_four_evaluators_really_parallel_and_join_once(self):
        barrier = threading.Barrier(4)

        class ParallelLLM(DemoLLM):
            def generate(self, task, *args, **kwargs):
                if task.startswith("evaluate:"):
                    barrier.wait(timeout=5)
                return super().generate(task, *args, **kwargs)

        services = demo_services()
        services.llm = ParallelLLM()
        with patch("graph.fan_in", wraps=fan_in) as join:
            state = build_graph("parallel", services).invoke(initial_state(), {"max_concurrency": 4})
        self.assertEqual(join.call_count, 1)
        self.assertTrue(all(join.call_args.args[0][f"{p}_analysis"] for p in PERSPECTIVES))
        self.assertTrue(state["final_report"])

    def test_cli_saves_report_state_and_log(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdout", new_callable=io.StringIO):
            code = main(
                ["--demo", "--scenario", "retry_exhausted", "--output-dir", directory, "--log-level", "INFO"]
            )
            self.assertEqual(code, 0)
            reports = list(Path(directory).glob("*/report.pdf"))
            self.assertEqual(len(reports), 1)
            import pymupdf

            with pymupdf.open(reports[0]) as report:
                self.assertGreater(len(report), 1)
                self.assertIn("ITME", "".join(page.get_text() for page in report))
            self.assertEqual(list(Path(directory).glob("*/report.md")), [])
            self.assertEqual(len(list(Path(directory).glob("*/state.json"))), 1)
            logs = next(Path(directory).glob("logs/*.log")).read_text()
            for event in (
                "RUN_START",
                "QUERY_REWRITE",
                "RETRY_EXHAUSTED",
                "FAN_OUT",
                "FAN_IN",
                "COUNTER_RESULT",
                "REPORT_SAVED",
                "RUN_DONE",
            ):
                self.assertIn(event, logs)
        configure_logging("ERROR")

    def test_no_credentials_does_not_silently_use_demo(self):
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "", "TAVILY_API_KEY": ""}),
            tempfile.TemporaryDirectory() as directory,
        ):
            env_file = Path(directory) / "empty.env"
            env_file.write_text("", encoding="utf-8")
            self.assertEqual(
                main(["--run", "--env-file", str(env_file), "--output-dir", directory]),
                1,
            )
            self.assertEqual(list(Path(directory).glob("*/report.pdf")), [])
        configure_logging("ERROR")

    def test_show_graph_does_not_need_providers(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["--show-graph"]), 0)
        for name in ("fan_in", "retry_limit", "record_first_missing", "record_second_missing"):
            self.assertIn(name, output.getvalue())


if __name__ == "__main__":
    unittest.main()
