#!/usr/bin/env python3
"""Small LLM experiment for optimized AG batching quality/cost.

This experiment compares:
  1. one query per LLM call
  2. family-packed queries per LLM call

It intentionally samples known bug neighborhoods rather than running every
optimized pair, so the result is a feasibility/cost-quality probe.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = Path("/home/smy/rtl_bug_agent")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import plan_llm_batches as batch_plan  # noqa: E402
from rtl_bug_agent.env import load_dotenv, make_client  # noqa: E402


TARGETS = {
    "hmac": {
        "family:hmac_cfg_lifecycle": ["cfg_block", "update_secret_key"],
        "family:hmac_error_reporting": ["invalid_config", "err_code", "invalid_config_atstart"],
    },
    "aes": {
        "family:aes_selector_control": ["key_words_sel", "key_full_sel", "combine_sparse"],
        "family:aes_integrity_error": ["mux_sel_err", "sp2v", "sel_buf"],
    },
}


SYSTEM = """你是硬件安全验证工程师。你会收到若干独立的 AG checking tasks；每个 task 是一个 assumption/uncertain query 以及它的候选 guarantees。
这些 task 被放在同一个 batch 里主要是为了减少调用成本，也可能共享上下文；但它们不一定相关。

硬性判断规则：
1. 必须逐个 query 独立输出 verdict，不要因为同 batch 中其他 query 存在就改变单个 query 的结论。
2. 只有当其他 query 明确共享信号、共享 guarantee、共享 source 或处于同一控制/数据路径时，才允许使用 cross-item context。
3. 如果没有使用跨项上下文，cross_item_context_used 必须为 false，cross_item_context_ids 必须为空数组。
4. 不要强行建立同 batch 内 query 的联系；无关 query 应完全独立判断。
5. 重点关注跨 chunk 协议、控制信号生命周期、错误传播和安全检测，不要因为语义相关就过度报 bug。

输出必须是 JSON。不要输出任何解释性前言、推理过程、Markdown 或代码块。第一个字符必须是 {，最后一个字符必须是 }。"""


def prompt_for_units(units: list[dict[str, Any]]) -> str:
    lines = [
        "请分析以下 AG candidate batch。注意：batch 内 task 可能相关也可能无关。",
        "你必须逐个 query 独立判断；只有在明确共享信号/guarantee/source/控制数据路径时，才使用其他 query 作为辅助上下文。",
        "verdict 只能是 SATISFIED, GAP, CONTRADICTION, DEFENSIVE, UNCERTAIN。",
        "如果认为有潜在 bug，请给出 debug_signal_path 和 concise_bug_reason。",
        "每个 result 必须包含 cross_item_context_used 和 cross_item_context_ids。",
        "输出 JSON 格式：",
        '{"results":[{"query_atom_id":"...","verdict":"...","relevant_guarantee_ids":["..."],"cross_item_context_used":false,"cross_item_context_ids":[],"debug_signal_path":"...","concise_bug_reason":"..."}]}',
        "只输出上述 JSON 对象，不要写分析过程。",
        "",
    ]
    for idx, unit in enumerate(units, start=1):
        lines.append(f"=== QUERY_GROUP_ITEM {idx} topic={unit['topic']} family={unit['family']} ===")
        lines.append(unit["text"])
        lines.append("")
    return "\n".join(lines)


def select_units(module: str, max_per_family: int) -> list[dict[str, Any]]:
    data = batch_plan.load_pairs(module)
    units = batch_plan.query_units(data)
    for unit in units:
        unit["family"] = batch_plan.infer_family(module, unit["topic"], unit["query"])

    selected: list[dict[str, Any]] = []
    for family, keywords in TARGETS[module].items():
        fam_units = [u for u in units if u["family"] == family]
        scored = []
        for unit in fam_units:
            haystack = (
                unit["query"]["atom_id"]
                + " "
                + unit["query"]["text"]
                + " "
                + " ".join(unit["query"].get("signals", []) or [])
                + " "
                + " ".join(m["text"] for m in unit["matches"])
            ).lower()
            score = sum(1 for kw in keywords if kw.lower() in haystack)
            if score:
                scored.append((score, unit))
        scored.sort(key=lambda item: (-item[0], item[1]["query"]["atom_id"]))
        selected.extend(unit for _, unit in scored[:max_per_family])

    # Deduplicate while preserving order.
    seen = set()
    out = []
    for unit in selected:
        atom_id = unit["query"]["atom_id"]
        if atom_id in seen:
            continue
        seen.add(atom_id)
        out.append(unit)
    return out


def unit_is_dense_fallback(unit: dict[str, Any]) -> bool:
    return any(m.get("pair_type") == "uncertain_dense_fallback" for m in unit.get("matches", []))


def unit_signal_roots(unit: dict[str, Any]) -> set[str]:
    roots = set()
    for sig in unit["query"].get("signals", []) or []:
        roots.add(batch_plan.normalize_signal(sig).lstrip("!~"))
    for match in unit.get("matches", []):
        for sig in match.get("shared_signals", []) or []:
            roots.add(batch_plan.normalize_signal(sig).lstrip("!~"))
    return {r for r in roots if r}


def compatible_with_batch(unit: dict[str, Any], batch: list[dict[str, Any]], min_shared_roots: int) -> bool:
    if not batch or min_shared_roots <= 0:
        return True
    roots = unit_signal_roots(unit)
    if not roots:
        return not unit_is_dense_fallback(unit)
    batch_roots = set()
    for item in batch:
        batch_roots |= unit_signal_roots(item)
    return len(roots & batch_roots) >= min_shared_roots


def batch_est_tokens(batch: list[dict[str, Any]]) -> int:
    return batch_plan.estimate_tokens(SYSTEM + "\n" + prompt_for_units(batch))


def make_batches(
    units: list[dict[str, Any]],
    strategy: str,
    max_queries: int,
    max_prompt_tokens: int,
    max_dense_fallback_uncertain: int,
    min_shared_roots: int,
    max_signal_roots: int,
) -> list[list[dict[str, Any]]]:
    if strategy == "one_query":
        return [[u] for u in units]
    if strategy == "family_pack":
        batches = []
        by_family: dict[str, list[dict[str, Any]]] = {}
        for unit in units:
            by_family.setdefault(unit["family"], []).append(unit)
        for family in sorted(by_family):
            chunk: list[dict[str, Any]] = []
            for unit in by_family[family]:
                candidate = chunk + [unit]
                dense_count = sum(1 for u in candidate if unit_is_dense_fallback(u))
                if (
                    chunk
                    and (
                        len(candidate) > max_queries
                        or batch_est_tokens(candidate) > max_prompt_tokens
                        or dense_count > max_dense_fallback_uncertain
                        or len(set().union(*(unit_signal_roots(u) for u in candidate))) > max_signal_roots
                        or not compatible_with_batch(unit, chunk, min_shared_roots)
                    )
                ):
                    batches.append(chunk)
                    chunk = [unit]
                else:
                    chunk = candidate
            if chunk:
                batches.append(chunk)
        return batches
    raise ValueError(strategy)


def parse_jsonish(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def fallback_extract(text: str, query_atom_ids: list[str]) -> dict[str, Any]:
    """Best-effort extraction when the model ignores JSON-only instructions."""
    results = []
    verdicts = ["CONTRADICTION", "DEFENSIVE", "SATISFIED", "UNCERTAIN", "GAP"]
    for atom_id in query_atom_ids:
        idx = text.find(atom_id)
        window = text[idx : idx + 1200] if idx >= 0 else text[:1200]
        verdict = "UNCERTAIN"
        for cand in verdicts:
            if cand in window:
                verdict = cand
                break
        results.append(
            {
                "query_atom_id": atom_id,
                "verdict": verdict,
                "relevant_guarantee_ids": [],
                "debug_signal_path": "",
                "concise_bug_reason": window[:500],
                "_fallback_extracted": True,
            }
        )
    return {"results": results, "_fallback": True}


def run_strategy(
    client,
    units: list[dict[str, Any]],
    strategy: str,
    max_queries: int,
    max_prompt_tokens: int,
    max_dense_fallback_uncertain: int,
    min_shared_roots: int,
    max_signal_roots: int,
    max_tokens: int,
) -> dict[str, Any]:
    batches = make_batches(
        units,
        strategy,
        max_queries,
        max_prompt_tokens,
        max_dense_fallback_uncertain,
        min_shared_roots,
        max_signal_roots,
    )
    start_stats = client.stats().copy()
    outputs = []
    t0 = time.monotonic()
    for idx, batch in enumerate(batches):
        print(
            f"  LLM {strategy} batch {idx + 1}/{len(batches)} "
            f"queries={len(batch)} pairs={sum(u['pair_count'] for u in batch)}",
            flush=True,
        )
        prompt = prompt_for_units(batch)
        content = client.chat(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
        )
        try:
            parsed = parse_jsonish(content)
        except Exception as exc:  # noqa: BLE001
            parsed = fallback_extract(content, [u["query"]["atom_id"] for u in batch])
            parsed["_parse_error"] = str(exc)
            parsed["_raw_prefix"] = content[:2000]
        outputs.append(
            {
                "batch_id": idx,
                "strategy": strategy,
                "query_atom_ids": [u["query"]["atom_id"] for u in batch],
                "families": sorted({u["family"] for u in batch}),
                "dense_fallback_uncertain_count": sum(1 for u in batch if unit_is_dense_fallback(u)),
                "signal_roots": sorted(set().union(*(unit_signal_roots(u) for u in batch))),
                "estimated_prompt_tokens": batch_plan.estimate_tokens(SYSTEM + "\n" + prompt),
                "parsed": parsed,
            }
        )
    end_stats = client.stats().copy()
    delta = {
        key: end_stats.get(key, 0) - start_stats.get(key, 0)
        for key in ("call_count", "error_count", "total_input_tokens", "total_output_tokens", "total_tokens")
    }
    delta["wall_seconds"] = round(time.monotonic() - t0, 1)
    return {
        "strategy": strategy,
        "batch_count": len(batches),
        "query_count": len(units),
        "pair_count": sum(u["pair_count"] for u in units),
        "estimated_prompt_tokens": sum(o["estimated_prompt_tokens"] for o in outputs),
        "usage_delta": delta,
        "outputs": outputs,
    }


def verdict_summary(result: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for out in result["outputs"]:
        parsed = out.get("parsed", {})
        for item in parsed.get("results", []) if isinstance(parsed, dict) else []:
            verdict = item.get("verdict", "MISSING")
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def cross_context_summary(result: dict[str, Any]) -> dict[str, int]:
    counts = {"used": 0, "not_used": 0, "missing": 0}
    for out in result["outputs"]:
        parsed = out.get("parsed", {})
        for item in parsed.get("results", []) if isinstance(parsed, dict) else []:
            if "cross_item_context_used" not in item:
                counts["missing"] += 1
            elif item.get("cross_item_context_used"):
                counts["used"] += 1
            else:
                counts["not_used"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modules", nargs="+", default=["hmac", "aes"])
    parser.add_argument("--provider", default="GUOCHUANG_DEEPSEEK")
    parser.add_argument("--max-per-family", type=int, default=3)
    parser.add_argument("--max-queries-per-batch", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=5500)
    parser.add_argument("--max-dense-fallback-uncertain", type=int, default=1)
    parser.add_argument("--min-shared-roots", type=int, default=1)
    parser.add_argument("--max-signal-roots", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument("--out", type=Path, default=HERE / "out/llm_batch_experiment.json")
    args = parser.parse_args()

    load_dotenv("/home/smy/.env")
    client = make_client(args.provider, thinking=None, timeout_s=args.timeout_s)

    all_results: dict[str, Any] = {
        "config": {
            "provider": args.provider,
            "max_per_family": args.max_per_family,
            "max_queries_per_batch": args.max_queries_per_batch,
            "max_prompt_tokens": args.max_prompt_tokens,
            "max_dense_fallback_uncertain": args.max_dense_fallback_uncertain,
            "min_shared_roots": args.min_shared_roots,
            "max_signal_roots": args.max_signal_roots,
            "max_tokens": args.max_tokens,
        },
        "modules": {},
    }

    for module in args.modules:
        units = select_units(module, args.max_per_family)
        module_result = {
            "selected_queries": [
                {
                    "atom_id": u["query"]["atom_id"],
                    "kind": u["query"]["kind"],
                    "family": u["family"],
                    "topic": u["topic"],
                    "pair_count": u["pair_count"],
                }
                for u in units
            ],
            "one_query": run_strategy(
                client,
                units,
                "one_query",
                args.max_queries_per_batch,
                args.max_prompt_tokens,
                args.max_dense_fallback_uncertain,
                args.min_shared_roots,
                args.max_signal_roots,
                args.max_tokens,
            ),
            "family_pack": run_strategy(
                client,
                units,
                "family_pack",
                args.max_queries_per_batch,
                args.max_prompt_tokens,
                args.max_dense_fallback_uncertain,
                args.min_shared_roots,
                args.max_signal_roots,
                args.max_tokens,
            ),
        }
        module_result["one_query"]["verdict_summary"] = verdict_summary(module_result["one_query"])
        module_result["family_pack"]["verdict_summary"] = verdict_summary(module_result["family_pack"])
        module_result["one_query"]["cross_context_summary"] = cross_context_summary(module_result["one_query"])
        module_result["family_pack"]["cross_context_summary"] = cross_context_summary(module_result["family_pack"])
        all_results["modules"][module] = module_result
        args.out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    all_results["client_final_stats"] = client.stats()
    args.out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    for module, result in all_results["modules"].items():
        print(f"\n[{module}] selected_queries={len(result['selected_queries'])}")
        for strategy in ["one_query", "family_pack"]:
            r = result[strategy]
            print(
                strategy,
                "batches=", r["batch_count"],
                "pairs=", r["pair_count"],
                "est_prompt=", r["estimated_prompt_tokens"],
                "usage=", r["usage_delta"],
                "verdicts=", r["verdict_summary"],
                "cross=", r["cross_context_summary"],
            )


if __name__ == "__main__":
    main()
