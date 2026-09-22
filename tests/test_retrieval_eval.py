import unittest
from unittest.mock import Mock

from rag.evaluate import evaluate_queries
from rag.pipeline import E5Embeddings


class RetrievalEvalTests(unittest.TestCase):
    def test_no_gold_means_no_invented_score(self):
        retriever = Mock()
        with self.assertRaises(ValueError):
            evaluate_queries(retriever, [{"id": "one", "query": "q", "technology": "ITME", "gold_pages": []}])
        retriever.search.assert_not_called()

    def test_hit_rate_and_reciprocal_rank(self):
        retriever = Mock()
        retriever.search.return_value = [
            {"document_id": "ITME", "page": 1, "chunk_id": "a", "content": "text"},
            {"document_id": "ITME", "page": 3, "chunk_id": "b", "content": "text"},
        ]
        output = evaluate_queries(
            retriever,
            [
                {
                    "id": "one",
                    "query": "q",
                    "technology": "ITME",
                    "gold_pages": [{"document_id": "ITME", "page": 3}],
                }
            ],
        )
        for mode in ("dense", "bm25", "hybrid"):
            self.assertEqual(output["scores"][mode], {"hit_rate_at_k": 1.0, "mrr_at_k": 0.5})

    def test_e5_prefix_normalization_and_length_guard(self):
        embedding = E5Embeddings.__new__(E5Embeddings)
        embedding.model = Mock()
        embedding.tokenizer = Mock()
        embedding.tokenizer.encode.return_value = [1, 2, 3]
        embedding.model.encode.return_value = [[1.0, 0.0]]
        embedding.embed_query("질문")
        self.assertEqual(embedding.model.encode.call_args.args[0], ["query: 질문"])
        self.assertTrue(embedding.model.encode.call_args.kwargs["normalize_embeddings"])
        embedding.embed_documents(["문서"])
        self.assertEqual(embedding.model.encode.call_args.args[0], ["passage: 문서"])
        embedding.tokenizer.encode.return_value = list(range(513))
        with self.assertRaises(ValueError):
            embedding.embed_query("overlong")
