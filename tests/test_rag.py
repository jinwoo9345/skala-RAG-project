"""인덱싱 로직은 작은 결정적 embedding으로 검증; 모델 정확도 테스트는 별도."""

import importlib.util
import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from config import Settings
from rag.pipeline import (
    build_hybrid_retriever,
    build_index,
    get_retriever,
    lexical_tokens,
    load_documents,
    split_documents,
)

AVAILABLE = all(importlib.util.find_spec(n) for n in ("pymupdf", "numpy", "rank_bm25"))


class WordTokenizer:
    def __call__(self, text, **kwargs):
        return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}

    def encode(self, text, add_special_tokens=False, **kwargs):
        return list(range(len(text.split()) + (2 if add_special_tokens else 0)))


class TinyEmbeddings:
    tokenizer = WordTokenizer()

    def __init__(self):
        self.document_calls = 0

    def embed_query(self, text):
        import numpy as np

        vector = np.array([1.0 + text.lower().count(x) for x in ("rdma", "cxl", "pim")], dtype="float32")
        return vector / np.linalg.norm(vector)

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self.embed_query(text) for text in texts]


@unittest.skipUnless(AVAILABLE, "uv sync --extra rag 필요")
class RagTests(unittest.TestCase):
    def fixture(self, root, text="ITME RDMA memory expansion and data transfer."):
        import pymupdf

        data = root / "data"
        data.mkdir(exist_ok=True)
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        pdf.save(data / "paper.pdf")
        pdf.close()
        manifest = data / "documents.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "technology": "ITME",
                        "role": "core",
                        "path": "data/paper.pdf",
                        "source_url": "https://example.test/paper",
                    }
                ]
            )
        )
        return replace(
            Settings(), manifest=manifest, index_dir=root / "index", chunk_tokens=20, overlap_tokens=3
        )

    def test_real_corpus_is_116_pages_and_preserves_metadata(self):
        documents = load_documents()
        self.assertEqual(len(documents), 116)
        self.assertEqual(
            {x["technology"] for x in documents},
            {"ITME", "CXL-PIM", "InfiniGen", "PagedAttention", "Mooncake", "CENT", "CacheGen"},
        )
        self.assertTrue(all(x["page"] > 0 and x["source_url"] for x in documents))

    def test_page_limit_and_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.fixture(Path(directory))
            with patch("rag.pipeline.MAX_DOCUMENT_PAGES", 0), self.assertRaises(ValueError):
                load_documents(settings.manifest)
            (Path(directory) / "data/paper.pdf").unlink()
            with self.assertRaises(FileNotFoundError):
                load_documents(settings.manifest)

    def test_chunk_ids_stable_page_boundaries_and_long_paragraph_coverage(self):
        source = {
            "technology": "ITME",
            "document_id": "ITME",
            "source": "ITME",
            "source_url": "https://example.test",
            "role": "core",
        }
        documents = [
            {**source, "page": page, "content": " ".join(f"word{i}" for i in range(90))} for page in (1, 2)
        ]
        settings = replace(Settings(), chunk_tokens=20, overlap_tokens=3)
        a = split_documents(documents, tokenizer=WordTokenizer(), settings=settings)
        b = split_documents(documents, tokenizer=WordTokenizer(), settings=settings)
        self.assertEqual(a, b)
        self.assertEqual(len({x["chunk_id"] for x in a}), len(a))
        for page in (1, 2):
            words = set(" ".join(x["content"] for x in a if x["page"] == page).split())
            self.assertEqual(words, {f"word{i}" for i in range(90)})
        self.assertTrue(all(len(x["content"].split()) <= 20 for x in a))

    def test_section_boundary(self):
        source = {
            "technology": "ITME",
            "document_id": "ITME",
            "page": 1,
            "content": "intro",
            "paragraphs": ["intro content", "2 Evaluation", "result here"],
        }
        chunks = split_documents([source], tokenizer=WordTokenizer())
        self.assertEqual(chunks[0]["content"], "intro content")
        self.assertTrue(chunks[1]["content"].startswith("2 Evaluation"))

    def test_index_cache_hit_source_change_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.fixture(root)
            embeddings = TinyEmbeddings()
            first = get_retriever(settings, embeddings=embeddings)
            self.assertEqual(embeddings.document_calls, 1)
            second = get_retriever(settings, embeddings=embeddings)
            self.assertEqual(embeddings.document_calls, 1)
            self.assertEqual(first.chunks, second.chunks)
            cache = next(settings.index_dir.iterdir())
            (cache / "chunks.json").write_text("broken")
            get_retriever(settings, embeddings=embeddings)
            self.assertEqual(embeddings.document_calls, 2)
            # PDF 내용이 바뀌면 fingerprint가 달라져야 한다.
            import pymupdf

            pdf_path = root / "data/paper.pdf"
            with pymupdf.open(pdf_path) as pdf:
                pdf[0].insert_text((72, 100), "Additional CXL memory content")
                pdf.saveIncr()
            get_retriever(settings, embeddings=embeddings)
            self.assertEqual(embeddings.document_calls, 3)
            self.assertEqual(len(list(settings.index_dir.iterdir())), 2)

    def test_hybrid_filters_deduplicates_and_supports_modes(self):
        chunks = [
            {
                "chunk_id": f"c{i}",
                "technology": "ITME" if i % 2 == 0 else "CXL-PIM",
                "role": "core",
                "content": text,
            }
            for i, text in enumerate(
                ["RDMA memory", "CXL PIM acceleration", "plain unrelated", "other text", "memory", "query"]
            )
        ]
        embeddings = TinyEmbeddings()
        index = build_index(chunks, embeddings)
        retriever = build_hybrid_retriever(index, chunks, embeddings, Settings())
        for mode in ("dense", "bm25", "hybrid"):
            results = retriever.search("RDMA", perspective="technical", technology="ITME", k=2, mode=mode)
            self.assertTrue(results)
            self.assertEqual(results[0]["chunk_id"], "c0")
            self.assertTrue(all(x["technology"] == "ITME" for x in results))
            self.assertEqual(len({x["chunk_id"] for x in results}), len(results))
        self.assertEqual(retriever.search("test", perspective="domain", role="supporting"), [])
        self.assertIn("cxl-pim", lexical_tokens("CXL-PIM RDMA TTFT 한국어"))


if __name__ == "__main__":
    unittest.main()
