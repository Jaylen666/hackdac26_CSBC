#!/usr/bin/env python3
"""Plan LLM batching for optimized AG pairing results.

This is an experiment-only cost/audit tool. It does not call an LLM.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


def estimate_tokens(text: str) -> int:
    """A conservative mixed Chinese/English token estimate.

    This intentionally avoids binding the experiment to a specific LLM tokenizer.
    Chinese-heavy spec text is counted close to one token per CJK char, while
    ASCII words are counted by rough subword length.
    """
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    non_cjk = CJK_RE.sub(" ", text)
    ascii_tokens = 0
    for item in WORD_RE.findall(non_cjk):
        if re.match(r"^[A-Za-z0-9_]+$", item):
            ascii_tokens += max(1, math.ceil(len(item) / 4))
        else:
            ascii_tokens += 1
    return cjk + ascii_tokens


def load_pairs(module: str) -> dict[str, Any]:
    path = HERE / f"out/optimized_pairs_{module}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def query_units(data: dict[str, Any]) -> list[dict[str, Any]]:
    units = []
    for item in data["results"]:
        matches = item.get("matches", [])
        if not matches:
            continue
        query = item["query"]
        topic = infer_topic(query, matches)
        unit_text = render_query_unit(query, matches)
        units.append(
            {
                "query": query,
                "matches": matches,
                "topic": topic,
                "pair_count": len(matches),
                "est_tokens": estimate_tokens(unit_text),
                "text": unit_text,
            }
        )
    return units


def normalize_signal(signal: str) -> str:
    sig = signal.strip()
    sig = re.sub(r"\[[^\]]+\]", "", sig)
    sig = re.sub(r"\.[A-Za-z0-9_]+$", "", sig)
    changed = True
    while changed:
        changed = False
        for prefix in ("mr_",):
            if sig.startswith(prefix):
                sig = sig[len(prefix):]
                changed = True
        for suffix in ("_ctrl", "_raw", "_sel", "_o", "_i", "_q", "_d"):
            if sig.endswith(suffix) and len(sig) > len(suffix):
                sig = sig[: -len(suffix)]
                changed = True
    return sig or signal


def infer_topic(query: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    for match in matches:
        shared = match.get("shared_signals") or []
        if shared:
            return "sig:" + normalize_signal(shared[0])
    signals = query.get("signals") or []
    if signals:
        return "query_sig:" + normalize_signal(signals[0])
    if query["kind"] == "uncertain":
        return "uncertain_dense:" + query["spec_id"].split("__")[0]
    return "misc:" + query["spec_id"].split("__")[0]


def infer_family(module: str, topic: str, query: dict[str, Any]) -> str:
    haystack = " ".join(
        [
            topic,
            query.get("atom_id", ""),
            query.get("spec_id", ""),
            query.get("text", ""),
            " ".join(query.get("signals", []) or []),
        ]
    ).lower()

    if module == "hmac":
        if any(k in haystack for k in ("cfg_block", "hash_start", "hash_continue", "reg_hash_done", "reg_hash_stop", "idle")):
            return "family:hmac_cfg_lifecycle"
        if any(k in haystack for k in ("err_code", "err_valid", "invalid_config", "msg_push_not_allowed", "hash_start_sha_disabled", "update_seckey")):
            return "family:hmac_error_reporting"
        if any(k in haystack for k in ("secret_key", "wipe_secret", "key_length", "reg2hw.key", "hw2reg.key")):
            return "family:hmac_key_wipe"
        if any(k in haystack for k in ("digest", "message_length", "fifo", "msg_")):
            return "family:hmac_msg_digest"
        if "hmac_reg_top" in haystack or "addr_hit" in haystack or "reg_steer" in haystack:
            return "family:hmac_reg_if"
        return "family:hmac_misc"

    if module == "aes":
        if any(k in haystack for k in ("state_sel", "add_rk", "key_full", "key_dec", "key_words", "round_key", "sparse", "mr_")):
            return "family:aes_selector_control"
        if any(k in haystack for k in ("sp2v", "mux_sel_err", "sel_buf", "err_o", "sp_enc_err")):
            return "family:aes_integrity_error"
        if any(k in haystack for k in ("prd", "mask", "share", "sbox")):
            return "family:aes_masking_prd"
        if any(k in haystack for k in ("state", "round", "crypt", "dec_key_gen")):
            return "family:aes_round_state"
        return "family:aes_misc"

    return "family:misc"


def render_query_unit(query: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    lines = [
        f"QUERY {query['atom_id']} kind={query['kind']}",
        f"query_signals={query.get('signals', [])}",
        f"query_text={query.get('text', '')}",
        "CANDIDATE_GUARANTEES:",
    ]
    for match in matches:
        lines.extend(
            [
                (
                    f"- rank={match['rank']} score={match['score']:.4f} "
                    f"dense={match['dense_score']:.4f} "
                    f"signal_relation={match['signal_relation_score']:.4f} "
                    f"relation_kind={match['signal_relation_kind']} "
                    f"shared={match.get('shared_signals', [])}"
                ),
                f"  guarantee_id={match['atom_id']}",
                f"  guarantee_signals={match.get('signals', [])}",
                f"  guarantee_text={match.get('text', '')}",
            ]
        )
    return "\n".join(lines)


def pack_units(
    units: list[dict[str, Any]],
    *,
    max_queries: int,
    max_prompt_tokens: int,
    group_key: str | None,
) -> list[dict[str, Any]]:
    prompt_overhead = 700
    per_query_overhead = 80

    if group_key:
        ordered = sorted(units, key=lambda u: (u[group_key], u["topic"], u["query"]["kind"], u["query"]["atom_id"]))
    else:
        ordered = sorted(units, key=lambda u: (u["query"]["kind"], u["query"]["atom_id"]))

    batches: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    cur_tokens = prompt_overhead
    cur_topic = None

    def flush() -> None:
        nonlocal cur, cur_tokens, cur_topic
        if not cur:
            return
        batches.append(
            {
                "batch_id": len(batches),
                "topics": sorted({u["topic"] for u in cur}),
                "query_count": len(cur),
                "pair_count": sum(u["pair_count"] for u in cur),
                "est_prompt_tokens": cur_tokens,
                "queries": [
                    {
                        "atom_id": u["query"]["atom_id"],
                        "kind": u["query"]["kind"],
                        "topic": u["topic"],
                        "pair_count": u["pair_count"],
                        "est_tokens": u["est_tokens"],
                    }
                    for u in cur
                ],
            }
        )
        cur = []
        cur_tokens = prompt_overhead
        cur_topic = None

    for unit in ordered:
        add_tokens = unit["est_tokens"] + per_query_overhead
        unit_group = unit[group_key] if group_key else None
        topic_break = group_key is not None and cur_topic is not None and unit_group != cur_topic
        size_break = len(cur) >= max_queries
        token_break = cur and cur_tokens + add_tokens > max_prompt_tokens
        if topic_break or size_break or token_break:
            flush()
        cur.append(unit)
        cur_tokens += add_tokens
        cur_topic = unit_group if group_key else None
    flush()
    return batches


def one_query_batches(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return pack_units(units, max_queries=1, max_prompt_tokens=1_000_000, group_key=None)


def summarize_batches(batches: list[dict[str, Any]]) -> dict[str, Any]:
    if not batches:
        return {
            "calls": 0,
            "total_prompt_tokens": 0,
            "avg_prompt_tokens": 0,
            "max_prompt_tokens": 0,
            "total_pairs": 0,
            "avg_queries_per_call": 0,
            "avg_pairs_per_call": 0,
        }
    total_tokens = sum(b["est_prompt_tokens"] for b in batches)
    total_queries = sum(b["query_count"] for b in batches)
    total_pairs = sum(b["pair_count"] for b in batches)
    return {
        "calls": len(batches),
        "total_prompt_tokens": total_tokens,
        "avg_prompt_tokens": round(total_tokens / len(batches), 1),
        "max_prompt_tokens": max(b["est_prompt_tokens"] for b in batches),
        "total_queries": total_queries,
        "total_pairs": total_pairs,
        "avg_queries_per_call": round(total_queries / len(batches), 2),
        "avg_pairs_per_call": round(total_pairs / len(batches), 2),
        "multi_topic_batches": sum(1 for b in batches if len(b["topics"]) > 1),
    }


def audit_batches(batches: list[dict[str, Any]], limit: int = 8) -> dict[str, Any]:
    largest = sorted(batches, key=lambda b: b["est_prompt_tokens"], reverse=True)[:limit]
    densest = sorted(batches, key=lambda b: b["pair_count"], reverse=True)[:limit]
    mixed = [b for b in batches if len(b["topics"]) > 1][:limit]
    return {
        "largest_batches": largest,
        "densest_batches": densest,
        "mixed_topic_batches": mixed,
    }


def plan_module(module: str, max_queries: int, max_prompt_tokens: int) -> dict[str, Any]:
    data = load_pairs(module)
    units = query_units(data)
    for unit in units:
        unit["family"] = infer_family(module, unit["topic"], unit["query"])
    strategies = {
        "one_query_per_call": one_query_batches(units),
        f"pack_{max_queries}_queries": pack_units(
            units,
            max_queries=max_queries,
            max_prompt_tokens=max_prompt_tokens,
            group_key=None,
        ),
        f"topic_pack_{max_queries}_queries": pack_units(
            units,
            max_queries=max_queries,
            max_prompt_tokens=max_prompt_tokens,
            group_key="topic",
        ),
        f"family_pack_{max_queries}_queries": pack_units(
            units,
            max_queries=max_queries,
            max_prompt_tokens=max_prompt_tokens,
            group_key="family",
        ),
    }
    return {
        "module": module,
        "input_metadata": data["metadata"],
        "non_empty_queries": len(units),
        "query_kind_counts": {
            "assumption": sum(1 for u in units if u["query"]["kind"] == "assumption"),
            "uncertain": sum(1 for u in units if u["query"]["kind"] == "uncertain"),
        },
        "pair_count": sum(u["pair_count"] for u in units),
        "strategies": {
            name: {
                "summary": summarize_batches(batches),
                "audit": audit_batches(batches),
            }
            for name, batches in strategies.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modules", nargs="+", default=["hmac", "aes"])
    parser.add_argument("--max-queries", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=6000)
    parser.add_argument("--out", type=Path, default=HERE / "out/llm_batch_plan.json")
    args = parser.parse_args()

    output = {
        "config": {
            "max_queries": args.max_queries,
            "max_prompt_tokens": args.max_prompt_tokens,
            "token_estimator": "cjk-aware rough estimate, prompt input only",
        },
        "modules": {
            module: plan_module(module, args.max_queries, args.max_prompt_tokens)
            for module in args.modules
        },
    }
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    for module, section in output["modules"].items():
        print(f"\n[{module}] non_empty_queries={section['non_empty_queries']} pairs={section['pair_count']}")
        for name, strat in section["strategies"].items():
            print(name, json.dumps(strat["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
