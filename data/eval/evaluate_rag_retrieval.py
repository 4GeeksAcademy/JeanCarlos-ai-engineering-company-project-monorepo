"""Recall@3 of the HealthCore knowledge base against data/eval/test-queries.json.

    python data/eval/evaluate_rag_retrieval.py                 # retrieval only (embeddings + Qdrant)
    python data/eval/evaluate_rag_retrieval.py --with-answers  # also generate answers (LLM)

Needs the collection indexed by `python -m data.process.rag`. Writes
data/eval/rag_retrieval_results.json (and data/eval/rag_answers.md with --with-answers).

- Recall@3: share of `queries` whose expected chunk (source_document + chunk_index) is in
  the top 3 of retrieve(question, k=3, min_score=0). The BRIEFING requires >= 0.80.
- Threshold evidence: the similarity of each expected chunk (must stay >= min_score) and the
  best score of each off-topic question (should stay < min_score), used to pick MIN_SCORE.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.rag import configured_min_score, generate_answer, retrieve  # noqa: E402

EVAL_DIR = REPO_ROOT / "data" / "eval"


def evaluate(spec: dict, with_answers: bool) -> tuple[dict, list[str]]:
    k = spec["k"]
    threshold = configured_min_score()
    rows, hits = [], 0
    for item in spec["queries"]:
        results = retrieve(item["question"], k=max(k, 5), min_score=0.0)
        expected = item["expected"]
        rank = next((i + 1 for i, r in enumerate(results)
                     if r["source_document"] == expected["source_document"] and r["chunk_index"] == expected["chunk_index"]),
                    None)
        hit = rank is not None and rank <= k
        hits += hit
        rows.append({
            "id": item["id"], "question": item["question"], "expected": f"{expected['source_document']}#{expected['chunk_index']}",
            "rank": rank, "hit_at_k": hit,
            "expected_score": next((round(r["score"], 4) for r in results
                                    if r["source_document"] == expected["source_document"]
                                    and r["chunk_index"] == expected["chunk_index"]), None),
            "top": [f"{r['source_document']}#{r['chunk_index']} ({r['score']:.3f})" for r in results[:k]],
        })
    probes = []
    for group in ("off_topic", "unanswerable_in_domain"):
        for item in spec.get(group, []):
            results = retrieve(item["question"], k=k, min_score=0.0)
            best = results[0] if results else None
            probes.append({"id": item["id"], "group": group, "question": item["question"],
                           "best": f"{best['source_document']}#{best['chunk_index']}" if best else None,
                           "best_score": round(best["score"], 4) if best else None,
                           "chunks_above_min_score": sum(r["score"] >= threshold for r in results)})

    expected_scores = [r["expected_score"] for r in rows if r["expected_score"] is not None]
    off_topic = [p["best_score"] for p in probes if p["group"] == "off_topic" and p["best_score"] is not None]
    result = {
        "collection": spec["collection"], "k": k, "questions": len(rows),
        "documents_covered": sorted({r["expected"].split("#")[0] for r in rows}),
        "recall_at_k": hits / len(rows), "target": spec["target_recall_at_k"],
        "passed": hits / len(rows) >= spec["target_recall_at_k"],
        "mrr": sum(1 / r["rank"] for r in rows if r["rank"]) / len(rows),
        "min_score": threshold,
        "expected_chunk_score": {"min": min(expected_scores), "max": max(expected_scores)},
        "off_topic_best_score": {"max": max(off_topic)} if off_topic else None,
        "expected_chunks_below_min_score": [r["id"] for r in rows if (r["expected_score"] or 0) < threshold],
        "queries": rows, "probes": probes,
    }

    answers: list[str] = []
    if with_answers:
        for item in [*spec["queries"], *spec.get("unanswerable_in_domain", []), *spec.get("off_topic", [])]:
            context = retrieve(item["question"])
            sources = ", ".join(f"{c['source_document']}#{c['chunk_index']} ({c['score']:.2f})" for c in context) or "ninguno"
            answers.append(f"### {item['id']} — {item['question']}\n\n_Chunks sobre el umbral: {sources}_\n\n"
                           f"{generate_answer(item['question'], context)}\n")
    return result, answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--queries", type=Path, default=EVAL_DIR / "test-queries.json")
    parser.add_argument("--with-answers", action="store_true", help="also call the generation model")
    args = parser.parse_args(argv)

    spec = json.loads(args.queries.read_text(encoding="utf-8"))
    result, answers = evaluate(spec, args.with_answers)
    (EVAL_DIR / "rag_retrieval_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if answers:
        (EVAL_DIR / "rag_answers.md").write_text(
            "# Respuestas generadas sobre test-queries.json\n\n"
            "Generadas con `python data/eval/evaluate_rag_retrieval.py --with-answers` "
            f"(min_score={result['min_score']}).\n\n" + "\n".join(answers), encoding="utf-8")

    for row in result["queries"]:
        print(f"{'HIT ' if row['hit_at_k'] else 'MISS'} {row['id']} rank={row['rank']} "
              f"score={row['expected_score']} top={row['top']}")
    for probe in result["probes"]:
        print(f"{probe['group']:<23} {probe['id']} best={probe['best']} score={probe['best_score']} "
              f"above_threshold={probe['chunks_above_min_score']}")
    print(f"Recall@{result['k']} = {result['recall_at_k']:.2%} (target {result['target']:.0%}) "
          f"MRR={result['mrr']:.3f} expected-chunk scores {result['expected_chunk_score']} "
          f"off-topic max {result['off_topic_best_score']} min_score={result['min_score']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
