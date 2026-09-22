"""페이지 보존 PDF → E5 Dense + BM25 → RRF. 캐시에는 pickle을 사용하지 않는다."""

import hashlib
import json
import re
import tempfile
from pathlib import Path

from config import DOCUMENT_MANIFEST, INDEX_DIR, MAX_DOCUMENT_PAGES, Settings
from workflow_logging import get_logger, log_operation

SPLITTER_VERSION = "page-column-section-token-v2"


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _manifest_rows(manifest):
    manifest = Path(manifest).resolve()
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("문서 manifest는 비어 있지 않은 목록이어야 함")
    resolved, seen = [], set()
    for row in rows:
        path = Path(row["path"])
        # 기존 manifest의 data/papers/... 경로는 프로젝트 기준.
        path = path if path.is_absolute() else manifest.parent.parent / path
        path = path.resolve()
        document_id = row.get("document_id", row["technology"])
        if document_id in seen or row["role"] not in ("core", "supporting"):
            raise ValueError("중복 document_id 또는 잘못된 문서 role")
        if not path.is_file():
            raise FileNotFoundError(f"입력 PDF 없음: {path.name}")
        seen.add(document_id)
        resolved.append({**row, "path": path, "document_id": document_id})
    return resolved


def _ordered_blocks(page):
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    # 두 단 PDF는 전체 너비 제목 사이 구간별로 왼쪽 열 다음 오른쪽 열을 읽는다.
    middle = page.rect.width / 2
    wide = sorted([b for b in blocks if b[0] < middle - 20 and b[2] > middle + 20], key=lambda b: b[1])
    narrow = [b for b in blocks if b not in wide]
    ordered = []
    for separator in [*wide, None]:
        band = [b for b in narrow if separator is None or b[1] < separator[1]]
        narrow = [b for b in narrow if b not in band]
        left = sorted([b for b in band if b[0] < middle], key=lambda b: b[1])
        right = sorted([b for b in band if b[0] >= middle], key=lambda b: b[1])
        ordered.extend(left + right)
        if separator is not None:
            ordered.append(separator)
    return ordered


@log_operation("PDF_LOAD")
def load_documents(manifest: Path = DOCUMENT_MANIFEST) -> list[dict]:
    import pymupdf

    rows = _manifest_rows(manifest)
    counts = []
    for row in rows:
        with pymupdf.open(row["path"]) as pdf:
            counts.append(len(pdf))
    if sum(counts) > MAX_DOCUMENT_PAGES:
        raise ValueError(f"문서 합계가 {MAX_DOCUMENT_PAGES}페이지 상한 초과")
    documents = []
    for row, count in zip(rows, counts):
        with pymupdf.open(row["path"]) as pdf:
            for number, page in enumerate(pdf, 1):
                paragraphs = []
                for block in _ordered_blocks(page):
                    text = block[4].replace("\u00ad", "")
                    text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    if text and not re.fullmatch(r"\d+", text):
                        paragraphs.append(text)
                if not paragraphs:
                    get_logger().warning("PDF_EMPTY_PAGE | document=%s | page=%d", row["document_id"], number)
                    continue
                documents.append(
                    {
                        "technology": row["technology"],
                        "document_id": row["document_id"],
                        "source": row.get("title") or row["document_id"],
                        "source_url": row.get("source_url", ""),
                        "role": row["role"],
                        "page": number,
                        "paragraphs": paragraphs,
                        "content": "\n\n".join(paragraphs),
                        "author": row.get("author", ""),
                        "year": row.get("year", ""),
                        "published_date": row.get("published_date", ""),
                        "source_type": row.get("source_type", "paper"),
                        "venue": row.get("venue", ""),
                        "identifier": row.get("identifier", ""),
                        "site_name": row.get("site_name", ""),
                    }
                )
        get_logger().info("PDF_READY | document=%s | pages=%d", row["document_id"], count)
    if not documents:
        raise ValueError("PDF에서 추출 가능한 텍스트 없음; OCR 필요")
    return documents


class E5Embeddings:
    def __init__(self, settings: Settings):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(
            settings.embedding_model,
            revision=settings.embedding_revision,
            cache_folder=str(settings.model_cache_dir),
        )
        self.tokenizer = self.model.tokenizer
        self.model.max_seq_length = 512

    def _encode(self, texts, prefix):
        values = [prefix + text for text in texts]
        if any(len(self.tokenizer.encode(x, add_special_tokens=True)) > 512 for x in values):
            raise ValueError("E5의 512 토큰 제한 초과; 조용한 truncation 방지")
        return self.model.encode(
            values, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, batch_size=16
        )

    @log_operation("EMBED_PASSAGES")
    def embed_documents(self, texts):
        return self._encode(texts, "passage: ")

    @log_operation("EMBED_QUERY")
    def embed_query(self, text):
        return self._encode([text], "query: ")[0]


@log_operation("EMBEDDING_LOAD")
def build_embeddings(settings=None):
    return E5Embeddings(settings or Settings.from_env())


def _token_windows(text, tokenizer, limit, overlap):
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, verbose=False)[
        "offset_mapping"
    ]
    if not offsets:
        return []
    if len(offsets) <= limit:
        return [text]
    pieces = []
    step = limit - overlap
    for start in range(0, len(offsets), step):
        stop = min(start + limit, len(offsets))
        begin_char = 0 if start == 0 else offsets[start][0]
        end_char = len(text) if stop == len(offsets) else offsets[stop][0]
        pieces.append(text[begin_char:end_char].strip())
        if stop == len(offsets):
            break
    return pieces


@log_operation("PDF_CHUNK")
def split_documents(documents: list, *, tokenizer=None, settings=None) -> list[dict]:
    settings = settings or Settings.from_env()
    if tokenizer is None:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            settings.embedding_model,
            revision=settings.embedding_revision,
            use_fast=True,
            cache_dir=str(settings.model_cache_dir),
        )
    chunks = []
    for document in documents:
        segments = []
        for paragraph in document.get("paragraphs", [document["content"]]):
            segments.extend(
                _token_windows(paragraph, tokenizer, settings.chunk_tokens, settings.overlap_tokens)
            )
        packed, current = [], ""
        for segment in segments:
            # 절 제목은 이전 절과 합치지 않는다. 원문의 block/문단 경계를 우선한다.
            if current and (
                re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z가-힣]", segment)
                or segment.lower() in ("abstract", "references", "conclusion")
            ):
                packed.append(current)
                current = ""
            candidate = current + "\n\n" + segment if current else segment
            if (
                len(tokenizer.encode(candidate, add_special_tokens=False, verbose=False))
                <= settings.chunk_tokens
            ):
                current = candidate
            else:
                if current:
                    packed.append(current)
                current = segment
        if current:
            packed.append(current)
        for i, content in enumerate(packed):
            # 특수 토큰 및 passage prefix까지 실제 tokenizer로 검사한다.
            if len(tokenizer.encode("passage: " + content, add_special_tokens=True)) > 512:
                raise ValueError("청크 토큰 한도 초과")
            identifier = f"{document['document_id']}-p{document['page']}-c{i}-{_digest(content.encode())[:8]}"
            metadata = {k: v for k, v in document.items() if k not in ("paragraphs", "content")}
            chunks.append({**metadata, "content": content, "chunk_id": identifier})
    if not chunks:
        raise ValueError("빈 청크 인덱스 생성 불가")
    get_logger().info(
        "CHUNKS_READY | pages=%d | chunks=%d | tokens=%d | overlap=%d",
        len(documents),
        len(chunks),
        settings.chunk_tokens,
        settings.overlap_tokens,
    )
    return chunks


class DenseIndex:
    """작은 코퍼스용 정확한 코사인 검색. 별도 OpenMP 런타임을 로드하지 않는다."""

    def __init__(self, vectors):
        import numpy as np

        vectors = np.asarray(vectors, dtype="float32")
        if vectors.ndim != 2 or not len(vectors) or not np.isfinite(vectors).all():
            raise ValueError("잘못된 임베딩 행렬")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("0벡터 임베딩은 검색에 사용할 수 없음")
        self.vectors = vectors / norms
        self.ntotal = len(vectors)

    def search(self, queries, k):
        import numpy as np

        queries = np.asarray(queries, dtype="float32")
        norms = np.linalg.norm(queries, axis=1, keepdims=True)
        if not np.isfinite(queries).all() or (norms == 0).any():
            raise ValueError("잘못된 질의 임베딩")
        scores = (queries / norms) @ self.vectors.T
        ids = np.argsort(-scores, axis=1, kind="stable")[:, :k]
        return np.take_along_axis(scores, ids, axis=1), ids


@log_operation("INDEX_BUILD")
def build_index(chunks: list, embeddings, index_dir: Path = INDEX_DIR):
    import numpy as np

    vectors = np.asarray(embeddings.embed_documents([x["content"] for x in chunks]), dtype="float32")
    if vectors.ndim != 2 or len(vectors) != len(chunks) or not np.isfinite(vectors).all():
        raise ValueError("임베딩 개수/차원/유한성 검증 실패")
    return DenseIndex(vectors)


def lexical_tokens(text):
    return re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*|[가-힣]+", text.lower())


class HybridRetriever:
    def __init__(self, vectorstore, chunks, embeddings, settings):
        from rank_bm25 import BM25Okapi

        self.index, self.chunks, self.embeddings, self.settings = vectorstore, chunks, embeddings, settings
        self.bm25 = BM25Okapi([lexical_tokens(x["content"]) or ["__empty__"] for x in chunks])

    @log_operation("HYBRID_SEARCH")
    def search(self, query, *, perspective, technology=None, role=None, k=None, mode="hybrid"):
        import numpy as np

        if mode not in ("dense", "bm25", "hybrid"):
            raise ValueError("mode는 dense/bm25/hybrid")
        k = self.settings.top_k if k is None else k
        if k < 1:
            raise ValueError("k는 양수여야 함")
        eligible = {
            i
            for i, x in enumerate(self.chunks)
            if (technology is None or x["technology"] == technology) and (role is None or x["role"] == role)
        }
        if not eligible:
            return []
        rankings = []
        if mode in ("dense", "hybrid"):
            vector = np.asarray([self.embeddings.embed_query(query)], dtype="float32")
            # subset을 다 찾은 뒤 필터링: 다른 기술이 top-k를 독점하지 않게 한다.
            _, ids = self.index.search(vector, len(self.chunks))
            rankings.append([int(i) for i in ids[0] if int(i) in eligible][: self.settings.dense_k])
        if mode in ("bm25", "hybrid"):
            scores = self.bm25.get_scores(lexical_tokens(query))
            rankings.append(
                sorted((i for i in eligible if scores[i] > 0), key=lambda i: (-scores[i], i))[
                    : self.settings.bm25_k
                ]
            )
        fused = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking, 1):
                fused[idx] = fused.get(idx, 0.0) + 1 / (60 + rank)
        ids = sorted(fused, key=lambda idx: (-fused[idx], idx))[:k]
        results = [{**self.chunks[i], "perspective": perspective, "score": fused[i]} for i in ids]
        get_logger().info(
            "RETRIEVAL_RESULT | perspective=%s | mode=%s | eligible=%d | selected=%d",
            perspective,
            mode,
            len(eligible),
            len(results),
        )
        return results


def build_hybrid_retriever(vectorstore, chunks, embeddings=None, settings=None):
    settings = settings or Settings.from_env()
    return HybridRetriever(vectorstore, chunks, embeddings or build_embeddings(settings), settings)


@log_operation("RAG_PREPARE")
def get_retriever(settings=None, *, embeddings=None, rebuild=False):
    import numpy as np

    settings = settings or Settings.from_env()
    rows = _manifest_rows(settings.manifest)
    metadata = {
        "sources": [(r["document_id"], _digest(r["path"].read_bytes())) for r in rows],
        "manifest": _digest(Path(settings.manifest).read_bytes()),
        "model": settings.embedding_model,
        "revision": settings.embedding_revision,
        "chunk_tokens": settings.chunk_tokens,
        "overlap": settings.overlap_tokens,
        "splitter": SPLITTER_VERSION,
        "index_backend": "numpy-cosine-v1",
    }
    fingerprint = _digest(json.dumps(metadata, sort_keys=True).encode())
    cache = settings.index_dir / fingerprint
    embeddings = embeddings or build_embeddings(settings)
    if not rebuild and (cache / "meta.json").is_file():
        try:
            saved = json.loads((cache / "meta.json").read_text())
            raw_chunks = (cache / "chunks.json").read_bytes()
            raw_index = (cache / "vectors.npy").read_bytes()
            if (
                saved["metadata"] != json.loads(json.dumps(metadata))
                or saved["chunks_hash"] != _digest(raw_chunks)
                or saved["index_hash"] != _digest(raw_index)
            ):
                raise ValueError("캐시 무결성 불일치")
            chunks = json.loads(raw_chunks)
            index = DenseIndex(np.load(cache / "vectors.npy", allow_pickle=False))
            if index.ntotal != len(chunks):
                raise ValueError("캐시 벡터/청크 개수 불일치")
            get_logger().info("INDEX_CACHE_HIT | chunks=%d", len(chunks))
            return build_hybrid_retriever(index, chunks, embeddings, settings)
        except (ValueError, KeyError, OSError, RuntimeError):
            get_logger().warning("INDEX_CACHE_INVALID | 캐시 재생성")
    chunks = split_documents(
        load_documents(settings.manifest), tokenizer=embeddings.tokenizer, settings=settings
    )
    index = build_index(chunks, embeddings)
    cache.mkdir(parents=True, exist_ok=True)
    # meta를 마지막으로 교체한다. 중단/동시 실행의 불완전 캐시는 다음 실행에서 검출.
    with tempfile.TemporaryDirectory(dir=settings.index_dir) as temp:
        temp = Path(temp)
        raw_chunks = json.dumps(chunks, ensure_ascii=False).encode()
        (temp / "chunks.json").write_bytes(raw_chunks)
        np.save(temp / "vectors.npy", index.vectors, allow_pickle=False)
        (temp / "meta.json").write_text(
            json.dumps(
                {
                    "metadata": metadata,
                    "chunks_hash": _digest(raw_chunks),
                    "index_hash": _digest((temp / "vectors.npy").read_bytes()),
                }
            ),
            encoding="utf-8",
        )
        for name in ("chunks.json", "vectors.npy", "meta.json"):
            (temp / name).replace(cache / name)
    get_logger().info("INDEX_SAVED | chunks=%d", len(chunks))
    return build_hybrid_retriever(index, chunks, embeddings, settings)


def chunk_to_evidence(chunk, *, perspective, score=None):
    """미검증 검색 후보. claim은 추출/검증 이전까지 반드시 비워 둔다."""
    return {
        **chunk,
        "evidence_id": chunk["chunk_id"],
        "perspective": perspective,
        "claim": "",
        "experimental_condition": "",
        "score": score,
    }
