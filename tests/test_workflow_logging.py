import io
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from workflow_logging import (
    ACTIVE_RUN,
    LOGGER_NAME,
    configure_logging,
    get_logger,
    log_node,
    log_operation,
    log_router,
)


class WorkflowLoggingTests(unittest.TestCase):
    def setUp(self):
        configure_logging("DEBUG")
        self.stream = io.StringIO()
        self.logger = logging.getLogger(LOGGER_NAME)
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()
        self.logger.addHandler(logging.StreamHandler(self.stream))

    def tearDown(self):
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

    def test_parallel_nested_operations_keep_run_id_and_hide_content(self):
        @log_operation("SEARCH")
        def search(query):
            return [query]

        def node(state):
            return {"technical_evidence": {"text": search("PRIVATE_CONTENT")}}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(log_node(name, node, get_logger(run)), {})
                for name, run in [("technical", "A"), ("domain", "B")]
            ]
            self.assertTrue(all(f.result() for f in futures))
        logs = self.stream.getvalue()
        self.assertIn("[run=A] OP_START | node=technical", logs)
        self.assertIn("[run=B] OP_START | node=domain", logs)
        self.assertNotIn("PRIVATE_CONTENT", logs)
        self.assertEqual(ACTIVE_RUN.get(), "standalone")

    def test_failure_preserves_exception_and_reports_location(self):
        error = RuntimeError("PRIVATE_ERROR_CONTENT")

        @log_operation("FAIL")
        def operation():
            raise error

        def node(state):
            return operation()

        with self.assertRaises(RuntimeError) as caught:
            log_node("technical", node, get_logger("failure"))({})
        self.assertIs(caught.exception, error)
        logs = self.stream.getvalue()
        self.assertIn("OP_FAILED", logs)
        self.assertIn("NODE_FAILED", logs)
        self.assertIn("test_workflow_logging.py:", logs)
        self.assertNotIn("PRIVATE_ERROR_CONTENT", logs)
        self.assertEqual(ACTIVE_RUN.get(), "standalone")

    def test_retry_limit_gap_is_visible(self):
        route = log_router(lambda state: "evaluate", get_logger("route"))
        state = {"missing_evidence": ["private"], "retry_count": 2, "max_retries": 2}
        self.assertEqual(route(state), "evaluate")
        self.assertIn("재검색=2/2", self.stream.getvalue())
        self.assertIn("EVIDENCE_GAP", self.stream.getvalue())

    def test_file_logging_and_reconfiguration_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.log"
            configure_logging("INFO", path)
            configure_logging("INFO", path)
            get_logger("file").info("ONCE")
            self.assertEqual(path.read_text().count("ONCE"), 1)


if __name__ == "__main__":
    unittest.main()
