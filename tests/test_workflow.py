import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main
from config import PERSPECTIVES
from demo import demo_services
from graph import build_graph
from state import initial_state
from supervisor import AGENTS
from workflow_logging import configure_logging


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        configure_logging("ERROR")

    def run_scenario(self, scenario="normal", max_retries=2, services=None, **state_options):
        services = services or demo_services(scenario)
        result = build_graph("test", services).invoke(
            initial_state(max_retries=max_retries, **state_options), {"recursion_limit": 100}
        )
        return result, services

    def test_graph_is_supervisor_hub_and_spoke(self):
        builder = build_graph("structure").builder
        # 하위 에이전트의 나가는 엣지는 supervisor뿐이고, 에이전트끼리 잇는 엣지는 없다.
        self.assertEqual(
            builder.edges, {("__start__", "supervisor"), *((name, "supervisor") for name in AGENTS)}
        )
        # supervisor의 분기는 add_conditional_edges 하나다.
        self.assertEqual(list(builder.branches), ["supervisor"])

    def test_normal_full_run_routes_each_agent_once_and_passes_quality(self):
        state, _ = self.run_scenario()
        self.assertEqual(set(state), set(initial_state()))
        self.assertEqual(state["node_status"], {name: "done" for name in AGENTS})
        self.assertEqual(state["rework_counts"], {})
        self.assertEqual(state["directive"]["target"], "end")
        self.assertTrue(state["eval_result"]["passed"])
        self.assertFalse(state["missing_evidence"])
        for perspective in PERSPECTIVES:
            technologies = {f["technology"] for f in state[f"{perspective}_analysis"]["findings"]}
            self.assertEqual(technologies, {"ITME", "CXL-PIM"})
        self.assertIn("DEMO / 테스트용", state["final_report"])
        self.assertNotIn("CITE:", state["final_report"])
        for heading in (
            "## SUMMARY",
            "## 1. 분석 배경 및 문제 정의",
            "## 2. 평가 대상 기술 선정",
            "## 3. 기술 개요",
            "## 4. 다관점 평가",
            "## 5. 관점 간 종합 및 시사점",
            "## 6. 분석 한계 및 신뢰성 확보",
            "### 4.4 데이터센터·클라우드 적용성",
            "## REFERENCE",
        ):
            self.assertIn(heading, state["final_report"])

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
        self.assertIn("- ITME · 기술 조사:", state["final_report"])

    def test_zero_retries(self):
        state, services = self.run_scenario("retry_exhausted", max_retries=0)
        self.assertEqual(state["retry_count"], 0)
        self.assertEqual(services.llm.technical_calls, {"ITME": 1, "CXL-PIM": 1})

    def test_insufficient_perspective_is_reworked_before_report(self):
        state, services = self.run_scenario("second_missing")
        # 근거가 부족한 관점(market)만 1회 재조사하고, 기술 조사는 다시 하지 않는다.
        self.assertEqual(state["rework_counts"], {"market": 1})
        self.assertEqual(services.llm.technical_calls["ITME"], 1)
        gaps = [x for x in state["missing_evidence"] if x["stage"] == 2]
        self.assertTrue(gaps and all(x["perspective"] == "market" and x["status"] == "recorded" for x in gaps))
        self.assertTrue(state["final_report"])

    def test_quality_failure_loops_once_then_passes(self):
        state, services = self.run_scenario("quality_fail")
        self.assertEqual(state["rework_counts"], {"quality": 1})
        self.assertEqual(services.llm.quality_calls, 2)
        self.assertTrue(state["eval_result"]["passed"])

    def test_step_limit_still_produces_report_and_ends(self):
        state, _ = self.run_scenario(max_steps=3)
        self.assertEqual(state["directive"]["target"], "end")
        self.assertTrue(state["final_report"])
        self.assertNotIn("quality_eval", state["node_status"])

    def test_agent_failure_is_recorded_retried_once_and_run_continues(self):
        services = demo_services()

        class BrokenWeb:
            def search(self, *args, **kwargs):
                raise RuntimeError("provider down")

        services.web = BrokenWeb()
        state, _ = self.run_scenario(services=services)
        self.assertEqual(state["node_status"]["market"], "failed")
        self.assertEqual(state["rework_counts"]["market"], 1)
        self.assertTrue(state["final_report"])

    def test_counter_found_is_cited_and_saved_in_final_references(self):
        state, _ = self.run_scenario("counter_found")
        self.assertTrue(all(x["status"] == "found" for x in state["counter_evidence"].values()))
        self.assertIn("DEMO 가상 제약 사항", state["final_report"])
        self.assertTrue(any(e.startswith("counter-") for r in state["references"] for e in r["evidence_ids"]))

    def test_cli_saves_report_state_and_log(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdout", new_callable=io.StringIO):
            env_file = Path(directory) / "test.env"
            env_file.write_text("LANGSMITH_TRACING=false\n", encoding="utf-8")
            code = main(
                [
                    "--demo",
                    "--scenario",
                    "retry_exhausted",
                    "--env-file",
                    str(env_file),
                    "--output-dir",
                    directory,
                    "--log-level",
                    "INFO",
                ]
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
                "SUPERVISOR_DECISION",
                "QUERY_REWRITE",
                "COUNTER_RESULT",
                "QUALITY_RESULT",
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
        for name in ("supervisor", *AGENTS):
            self.assertIn(name, output.getvalue())


if __name__ == "__main__":
    unittest.main()
