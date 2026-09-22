"""검색 평가: 사람이 지정한 gold_pages 없이는 점수를 만들어내지 않는다."""

import argparse
import json
from pathlib import Path

from config import PROJECT_ROOT, Settings
from rag.pipeline import get_retriever
from workflow_logging import configure_logging


def evaluate_queries(retriever, queries, *, k=5, inspect=False):
    rows = []
    for case in queries:
        gold = {(x["document_id"], x["page"]) for x in case.get("gold_pages", [])}
        if not inspect and not gold:
            raise ValueError(f"정답 페이지 라벨 필요: {case['id']}")
        for mode in ("dense", "bm25", "hybrid"):
            result = retriever.search(
                case["query"], perspective="retrieval_eval", technology=case["technology"], k=k, mode=mode
            )
            rank = next((r for r, x in enumerate(result, 1) if (x["document_id"], x["page"]) in gold), None)
            rows.append(
                {
                    "id": case["id"],
                    "mode": mode,
                    "hit": int(rank is not None) if gold else None,
                    "reciprocal_rank": 1 / rank if rank else (0 if gold else None),
                    "results": [
                        {
                            "document_id": x["document_id"],
                            "page": x["page"],
                            "chunk_id": x["chunk_id"],
                            "content": x["content"],
                        }
                        for x in result
                    ],
                }
            )
    scores = {}
    if not inspect:
        for mode in ("dense", "bm25", "hybrid"):
            subset = [x for x in rows if x["mode"] == mode]
            if subset:
                scores[mode] = {
                    "hit_rate_at_k": sum(x["hit"] for x in subset) / len(subset),
                    "mrr_at_k": sum(x["reciprocal_rank"] for x in subset) / len(subset),
                }
    return {"k": k, "scores": scores, "cases": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, default=PROJECT_ROOT / "data/retrieval_queries.json")
    parser.add_argument("--inspect", action="store_true", help="점수 없이 검색 후보와 페이지 출력")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k는 양수여야 함")
    queries = json.loads(args.queries.read_text())
    # 정답 누락을 모델 다운로드/임베딩보다 먼저 알린다.
    if not args.inspect and any(not x.get("gold_pages") for x in queries):
        parser.error("gold_pages를 직접 검토하여 입력하세요. --inspect로 후보 확인 가능.")
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    configure_logging()
    output = evaluate_queries(get_retriever(Settings.from_env()), queries, k=args.k, inspect=args.inspect)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
