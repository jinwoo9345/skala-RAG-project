import unittest
from unittest.mock import Mock

from config import DOMAIN_CRITERIA, EVALUATION_CRITERIA, TECHNICAL_EVIDENCE_ITEMS, Settings
from schemas import Extraction
from tools.llm import OpenAILLM
from tools.web_search import canonical_url, web_search


class ToolTests(unittest.TestCase):
    def test_report_criteria_match_design_document(self):
        self.assertEqual(len(TECHNICAL_EVIDENCE_ITEMS), 8)
        self.assertEqual(
            DOMAIN_CRITERIA,
            ("용량", "성능", "데이터 이동", "확장성", "비용과 구축 복잡도"),
        )
        self.assertEqual(
            EVALUATION_CRITERIA["stakeholder"],
            ("경쟁 진영", "도입 기업·개발자", "투자 업계"),
        )

    def test_web_normalizes_deduplicates_and_preserves_snippet_status(self):
        tool = Mock()
        date = "Mon, 14 Sep 2026 00:00:00 GMT"
        tool.search.return_value = [
            {
                "url": "https://example.test/p#x",
                "title": "paper",
                "content": "a useful search excerpt",
                "published_date": date,
            },
            {"url": "https://example.test/p#y", "content": "duplicate", "published_date": date},
            {"url": "javascript:alert(1)", "content": "bad", "published_date": date},
            {"url": "https://example.test/undated", "content": "no published date"},
            {"url": "https://example.test/blog/post", "content": "blog post", "published_date": date},
            {"url": "https://blog.example.test/post", "content": "blog host", "published_date": date},
        ]
        results = web_search(tool, "query", max_results=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_url"], "https://example.test/p")
        self.assertEqual(results[0]["content_type"], "snippet")
        self.assertEqual(results[0]["published_date"], "2026-09-14")
        tool.search.assert_called_once_with(query="query", topic="news", max_results=3, format_output=False)

    def test_web_error_is_not_not_found(self):
        tool = Mock()
        tool.search.side_effect = RuntimeError("provider failure")
        with self.assertRaises(RuntimeError):
            web_search(tool, "query")

    def test_english_url_replaces_mismatched_localized_title(self):
        tool = Mock()
        tool.search.return_value = [
            {
                "url": "https://zamin.uz/en/technology/204065-ai-memory-bottleneck-startup-raises-135-million.html",
                "title": "Сунъий интеллект хотира муаммосини ҳал этувчи стартап 135 млн доллар жалб қилди",
                "raw_content": (
                    "# AI memory bottleneck startup raises $135 million\n\n"
                    "Every time you send a prompt, data undergoes a relay race."
                ),
                "published_date": "Fri, 29 May 2026 00:00:00 GMT",
            }
        ]
        results = web_search(tool, "query")
        self.assertEqual(results[0]["source"], "AI memory bottleneck startup raises $135 million")

    def test_llm_uses_schema_and_store_false(self):
        client = Mock()
        client.responses.parse.return_value.output_parsed = Extraction(facts=[])
        client.responses.parse.return_value.usage = None
        result = OpenAILLM(Settings(), client).generate("test", "instruction", {}, Extraction)
        self.assertEqual(result.facts, [])
        self.assertFalse(client.responses.parse.call_args.kwargs["store"])
        self.assertIs(client.responses.parse.call_args.kwargs["text_format"], Extraction)

    def test_llm_refusal_stops(self):
        client = Mock()
        client.responses.parse.return_value.output_parsed = None
        with self.assertRaises(ValueError):
            OpenAILLM(Settings(), client).generate("test", "instruction", {}, Extraction)

    def test_unsafe_urls_rejected(self):
        self.assertFalse(canonical_url("file:///private/document"))
        self.assertFalse(canonical_url("https://secret@example.com/p"))
