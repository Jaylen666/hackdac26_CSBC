#!/usr/bin/env python3
"""Evaluate retrieved HMAC AG pairs against a small gold set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_RETRIEVAL = HERE / "out/retrieval_hmac.json"
DEFAULT_GOLD = HERE / "gold_hmac_ag_pairs.json"
DEFAULT_OUT = HERE / "out/eval_hmac.json"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _contains(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def _match_query(query: dict[str, Any], gold: dict[str, Any]) -> bool:
    if query.get("spec_id") != gold["query_spec"]:
        return False
    if query.get("kind") != gold.get("query_kind", query.get("kind")):
        return False
    needle = gold.get("query_text_contains")
    return not needle or _contains(query.get("text", ""), needle)


def _match_target(match: dict[str, Any], gold: dict[str, Any]) -> bool:
    if match.get("spec_id") != gold["target_spec"]:
        return False
    if match.get("kind") != gold.get("target_kind", match.get("kind")):
        return False
    needle = gold.get("target_text_contains")
    return not needle or _contains(match.get("text", ""), needle)


def evaluate(retrieval: dict[str, Any], gold_cases: list[dict[str, Any]]) -> dict[str, Any]:
    per_case = []
    for gold in gold_cases:
        candidates = [
            item for item in retrieval["results"] if _match_query(item["query"], gold)
        ]
        if not candidates:
            per_case.append(
                {
                    "case_id": gold["case_id"],
                    "found_query": False,
                    "hit_rank": None,
                    "top_matches": [],
                }
            )
            continue

        item = candidates[0]
        hit_rank = None
        for match in item["matches"]:
            if _match_target(match, gold):
                hit_rank = match["rank"]
                break

        per_case.append(
            {
                "case_id": gold["case_id"],
                "found_query": True,
                "query_atom_id": item["query"]["atom_id"],
                "hit_rank": hit_rank,
                "top_matches": item["matches"][:10],
            }
        )

    n = max(len(per_case), 1)
    metrics = {
        "num_cases": len(per_case),
        "recall@1": sum(1 for c in per_case if c["hit_rank"] is not None and c["hit_rank"] <= 1) / n,
        "recall@3": sum(1 for c in per_case if c["hit_rank"] is not None and c["hit_rank"] <= 3) / n,
        "recall@5": sum(1 for c in per_case if c["hit_rank"] is not None and c["hit_rank"] <= 5) / n,
        "recall@10": sum(1 for c in per_case if c["hit_rank"] is not None and c["hit_rank"] <= 10) / n,
        "mrr": sum((1 / c["hit_rank"]) for c in per_case if c["hit_rank"]) / n,
    }
    return {"metrics": metrics, "cases": per_case}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    result = evaluate(_load_json(args.retrieval), _load_json(args.gold))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result["metrics"], ensure_ascii=False, sort_keys=True))
    for case in result["cases"]:
        print(f"case={case['case_id']} hit_rank={case['hit_rank']}")
        for match in case.get("top_matches", [])[:5]:
            print(
                f"  #{match['rank']} score={match['score']:.4f} "
                f"spec={match['spec_id']} text={match['text'][:100]}"
            )


if __name__ == "__main__":
    main()
