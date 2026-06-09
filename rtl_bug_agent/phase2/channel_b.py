"""
Channel B: Assumption-Guarantee Pairing
========================================

For each signal that has both drivers (guarantees) and consumers (assumptions),
ask the LLM: "Does the driver's guarantee satisfy the consumer's assumption?"

This is the core channel of Phase 2 — most protocol-level and semantic bugs
manifest as an assumption that no guarantee covers.

Pairs are batched by signal to reduce LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2 import semantic_ag
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = _PROJECT_ROOT / "config/prompts/phase2/channel_b_ag_pairing.md"


def _call_with_retry(fn, attempts: int = 3, delay: float = 1.0):
    """Call *fn* with retries on LLM failures.  Returns None if all
    attempts are exhausted."""
    import time as _time
    for a in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if a == attempts:
                print(f"ERROR (retries exhausted) ({exc})")
                return None
            _time.sleep(delay * a)


def run_channel_b(
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 2500,
    workers: int = 4,
    checkpoint_path: str | None = None,
) -> list[dict[str, Any]]:
    """Run the A-G pairing channel across all signals in *graph*.

    *workers* parallel LLM calls.  Set to 1 for sequential.
    If *checkpoint_path* is given, findings are incrementally writen
    as JSONL lines (atomic append, one per signal) so a crash part-way
    through only loses in-flight work.
    """
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    pairs = graph.find_ag_pairs(filter_mode="behavioral")

    if not pairs:
        print("Channel B: no A-G pairs found.")
        return []

    by_signal: dict[str, list[dict[str, Any]]] = {}
    for p in pairs:
        sig = p["signal"]
        by_signal.setdefault(sig, []).append(p)

    items = sorted(by_signal.items())
    total = len(items)

    # Restore from checkpoint
    ckpt = _JsonlCheckpoint(checkpoint_path) if checkpoint_path else None
    all_findings: list[dict[str, Any]] = ckpt.load() if ckpt else []
    processed: set[str] = {f.get("_signal", "") for f in all_findings}
    remaining = [(s, sp) for s, sp in items if s not in processed]

    if processed:
        print(f"  Channel B: {len(processed)} signals from checkpoint, "
              f"{len(remaining)} remaining")

    if not remaining:
        print(f"  Channel B done: {len(all_findings)} total findings")
        return all_findings

    def _process_one(signal, signal_pairs):
        findings = _call_with_retry(
            lambda: _check_signal(
                signal, signal_pairs, graph, client, prompt_template, max_tokens
            ),
            attempts=3,
        )
        if findings is not None:
            for f in findings:
                f["_signal"] = signal
            # Always write a checkpoint line — even empty findings
            # must be recorded so the signal isn't re-processed.
            if ckpt:
                ckpt.append_all(findings or [{"_signal": signal, "_empty": True}])
        return findings, signal

    violations = 0
    uncertains = 0
    done_count = 0

    if workers <= 1:
        for sig, sp in remaining:
            print(
                f"  Channel B [signal] signal={sig} "
                f"({len(sp)} pairs) ... ",
                end="", flush=True,
            )
            findings, _ = _process_one(sig, sp)
            if findings is not None:
                all_findings.extend(findings)
                violations += sum(1 for f in findings if f.get("verdict") == "VIOLATION")
                uncertains += sum(1 for f in findings if f.get("verdict") == "UNCERTAIN")
                print(f"{len(findings)} findings")
            done_count += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, sig, sp): sig
                       for sig, sp in remaining}
            for future in as_completed(futures):
                sig = futures[future]
                try:
                    findings, _ = future.result()
                except Exception:
                    findings = None
                if findings is not None:
                    all_findings.extend(findings)
                    violations += sum(1 for f in findings if f.get("verdict") == "VIOLATION")
                    uncertains += sum(1 for f in findings if f.get("verdict") == "UNCERTAIN")
                done_count += 1
                print(
                    f"  Channel B [{done_count}/{len(remaining)}] signal={sig} "
                    f"({len(findings) if findings else 0} findings) ... done"
                )

    # Filter out checkpoint-only placeholder entries
    all_findings = [f for f in all_findings if not f.get("_empty")]
    print(
        f"  Channel B done: {len(all_findings)} total findings "
        f"({violations} violations, {uncertains} uncertain)"
    )
    return all_findings


def run_channel_b_semantic(
    pairing: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_path: str | Path = DEFAULT_PROMPT,
    max_tokens: int = 2500,
    workers: int = 4,
    checkpoint_path: str | None = None,
    batch_config: semantic_ag.SemanticBatchConfig | None = None,
) -> list[dict[str, Any]]:
    """Run Channel B over semantic query units.

    The legacy signal-grouped path above is intentionally unchanged.  This
    optional path consumes BGE-M3-retrieved query units, where each unit is
    one assumption/uncertain query plus its candidate guarantees.
    """
    prompt_template = Path(prompt_path).read_text(encoding="utf-8")
    units = semantic_ag.query_units(pairing)
    batch_cfg = batch_config or semantic_ag.SemanticBatchConfig(mode="single")
    if not units:
        print("Channel B semantic: no query units found.")
        return []

    ckpt = _JsonlCheckpoint(checkpoint_path) if checkpoint_path else None
    all_findings: list[dict[str, Any]] = ckpt.load() if ckpt else []
    processed: set[str] = {f.get("_semantic_unit", "") for f in all_findings}
    remaining = [u for u in units if u["unit_id"] not in processed]

    if processed:
        print(
            f"  Channel B semantic: {len(processed)} units from checkpoint, "
            f"{len(remaining)} remaining"
        )

    if not remaining:
        print(f"  Channel B semantic done: {len(all_findings)} total findings")
        return [f for f in all_findings if not f.get("_empty")]

    if batch_cfg.mode == "guarded":
        return _run_channel_b_semantic_batched(
            remaining,
            all_findings,
            ckpt,
            graph,
            client,
            prompt_template,
            max_tokens,
            workers,
            batch_cfg,
        )
    if batch_cfg.mode != "single":
        raise ValueError(f"unsupported semantic batch mode: {batch_cfg.mode}")

    def _process_one(unit):
        findings = _call_with_retry(
            lambda: _check_semantic_unit(unit, graph, client, prompt_template, max_tokens),
            attempts=3,
        )
        if findings is not None:
            for f in findings:
                f["_semantic_unit"] = unit["unit_id"]
                f["_semantic_query_kind"] = unit["query"].get("kind", "")
            if ckpt:
                ckpt.append_all(findings or [{"_semantic_unit": unit["unit_id"], "_empty": True}])
        return findings, unit

    violations = 0
    uncertains = 0
    done_count = 0

    if workers <= 1:
        for unit in remaining:
            print(
                f"  Channel B semantic unit={unit['unit_id']} "
                f"({len(unit['matches'])} matches) ... ",
                end="", flush=True,
            )
            findings, _ = _process_one(unit)
            if findings is not None:
                all_findings.extend(findings)
                violations += sum(1 for f in findings if f.get("verdict") == "VIOLATION")
                uncertains += sum(1 for f in findings if f.get("verdict") == "UNCERTAIN")
                print(f"{len(findings)} findings")
            done_count += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, unit): unit for unit in remaining}
            for future in as_completed(futures):
                unit = futures[future]
                try:
                    findings, _ = future.result()
                except Exception:
                    findings = None
                if findings is not None:
                    all_findings.extend(findings)
                    violations += sum(1 for f in findings if f.get("verdict") == "VIOLATION")
                    uncertains += sum(1 for f in findings if f.get("verdict") == "UNCERTAIN")
                done_count += 1
                print(
                    f"  Channel B semantic [{done_count}/{len(remaining)}] "
                    f"unit={unit['unit_id']} ({len(findings) if findings else 0} findings) ... done"
                )

    all_findings = [f for f in all_findings if not f.get("_empty")]
    print(
        f"  Channel B semantic done: {len(all_findings)} total findings "
        f"({violations} violations, {uncertains} uncertain)"
    )
    return all_findings


def _run_channel_b_semantic_batched(
    remaining_units: list[dict[str, Any]],
    all_findings: list[dict[str, Any]],
    ckpt: "_JsonlCheckpoint | None",
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
    workers: int,
    batch_config: semantic_ag.SemanticBatchConfig,
) -> list[dict[str, Any]]:
    batches = semantic_ag.make_batches(remaining_units, batch_config)
    summary = semantic_ag.summarise_batches(batches)
    print(
        "  Channel B semantic guarded batching: "
        f"{summary['calls']} calls for {summary['query_count']} query units "
        f"({summary['avg_queries_per_call']} avg queries/call)"
    )

    def _process_one(batch):
        findings = _call_with_retry(
            lambda: _check_semantic_batch(batch, graph, client, prompt_template, max_tokens),
            attempts=3,
        )
        if findings is not None:
            finding_units = {str(f.get("_semantic_unit", "")) for f in findings}
            for unit in batch["units"]:
                uid = unit["unit_id"]
                if uid not in finding_units:
                    findings.append({"_semantic_unit": uid, "_empty": True})
            if ckpt:
                ckpt.append_all(findings)
        return findings, batch

    violations = 0
    uncertains = 0
    done_count = 0

    if workers <= 1:
        for batch in batches:
            print(
                f"  Channel B semantic batch={batch['batch_id']} "
                f"({batch['query_count']} queries, {batch['pair_count']} pairs) ... ",
                end="", flush=True,
            )
            findings, _ = _process_one(batch)
            visible = [f for f in findings or [] if not f.get("_empty")]
            all_findings.extend(visible)
            violations += sum(1 for f in visible if f.get("verdict") == "VIOLATION")
            uncertains += sum(1 for f in visible if f.get("verdict") == "UNCERTAIN")
            print(f"{len(visible)} findings")
            done_count += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_one, batch): batch for batch in batches}
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    findings, _ = future.result()
                except Exception:
                    findings = None
                visible = [f for f in findings or [] if not f.get("_empty")]
                all_findings.extend(visible)
                violations += sum(1 for f in visible if f.get("verdict") == "VIOLATION")
                uncertains += sum(1 for f in visible if f.get("verdict") == "UNCERTAIN")
                done_count += 1
                print(
                    f"  Channel B semantic [{done_count}/{len(batches)}] "
                    f"batch={batch['batch_id']} queries={batch['query_count']} "
                    f"({len(visible)} findings) ... done"
                )

    all_findings = [f for f in all_findings if not f.get("_empty")]
    print(
        f"  Channel B semantic done: {len(all_findings)} total findings "
        f"({violations} violations, {uncertains} uncertain)"
    )
    return all_findings


# ------------------------------------------------------------------
# JSONL checkpoint helper
# ------------------------------------------------------------------


class _JsonlCheckpoint:
    """Thread-safe-ish JSONL checkpoint file.

    Each line is a self-contained JSON object.  Linux guarantees
    atomic append for writes under PIPE_BUF (4096 bytes), so concurrent
    single-line writes from multiple threads are safe without a lock.
    """

    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[dict[str, Any]]:
        p = Path(self.path)
        if not p.exists():
            return []
        findings: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    findings.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip corrupted line
        return findings

    def append_all(self, findings: list[dict[str, Any]]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            for fd in findings:
                line = json.dumps(fd, ensure_ascii=False)
                f.write(line + "\n")


def _check_signal(
    signal: str,
    signal_pairs: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Check all A-G pairs for one signal in a single LLM call."""

    # Gather all unique consumer assumptions and driver guarantees
    assumptions: list[dict[str, Any]] = []
    guarantees: list[dict[str, Any]] = []

    seen_assumptions: set[str] = set()
    seen_guarantees: set[str] = set()

    for pair in signal_pairs:
        # Deduplicate assumptions
        a_text = pair["assumption"].get("constraint", "")
        a_key = f"{pair['consumer_spec']}::{a_text[:80]}"
        if a_key not in seen_assumptions:
            seen_assumptions.add(a_key)
            assumptions.append(
                {
                    "spec_id": pair["consumer_spec"],
                    "constraint": a_text,
                    "bug_relevance": pair["assumption"].get("bug_relevance", ""),
                    "related_signals": pair["assumption"].get("related_signals", []),
                }
            )

        # Deduplicate guarantees
        for dg in pair.get("driver_guarantees", []):
            g_text = dg["guarantee"].get("property", "")
            g_key = f"{dg['spec_id']}::{g_text[:80]}"
            if g_key not in seen_guarantees:
                seen_guarantees.add(g_key)
                guarantees.append(
                    {
                        "spec_id": dg["spec_id"],
                        "property": g_text,
                        "output_signals": dg["guarantee"].get(
                            "output_signals", []
                        ),
                    }
                )

    # If there are no structured guarantees, fall back to behavior
    # of driver specs
    if not guarantees:
        for driver_id in graph.get_drivers(signal):
            spec = graph.specs.get(driver_id)
            if spec:
                guarantees.append(
                    {
                        "spec_id": driver_id,
                        "property": "[implicit from behavior]",
                        "behavior_excerpt": spec.get("behavior", "")[:400],
                    }
                )

    # Build the LLM payload
    context = {
        "signal": signal,
        "signal_context": _signal_context(graph, signal),
        "assumptions": assumptions,
        "guarantees": guarantees,
    }

    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )

    parsed = _parse_llm_response(content)
    return parsed.get("findings", [])


def _check_semantic_unit(
    unit: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    query = unit["query"]
    matches = unit["matches"]
    signal = _semantic_unit_signal(query, matches)
    assumptions = [
        {
            "spec_id": query.get("spec_id", ""),
            "constraint": query.get("text", ""),
            "bug_relevance": (
                "[UNCERTAIN — LLM flagged potential issue]"
                if query.get("kind") == "uncertain"
                else ""
            ),
            "related_signals": query.get("signals", []),
            "source_kind": query.get("kind", ""),
        }
    ]
    guarantees = [
        {
            "spec_id": match.get("spec_id", ""),
            "property": match.get("text", ""),
            "output_signals": match.get("signals", []),
            "rank": match.get("rank"),
            "score": match.get("score"),
            "pair_type": match.get("pair_type"),
            "shared_signals": match.get("shared_signals", []),
        }
        for match in matches
    ]
    context = {
        "signal": signal,
        "signal_context": _signal_context(graph, signal) if signal else "",
        "retrieval_method": "semantic_dense_signal_relation",
        "semantic_query": {
            "atom_id": query.get("atom_id", ""),
            "kind": query.get("kind", ""),
            "source_refs": query.get("source_refs", []),
        },
        "assumptions": assumptions,
        "guarantees": guarantees,
    }
    content = client.chat(
        messages=[
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )
    parsed = _parse_llm_response(content)
    return parsed.get("findings", [])


def _check_semantic_batch(
    batch: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    prompt_template: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Check a guarded batch of semantic query units in one LLM call."""
    tasks = []
    unit_by_id = {unit["unit_id"]: unit for unit in batch["units"]}
    for unit in batch["units"]:
        query = unit["query"]
        matches = unit["matches"]
        signal = _semantic_unit_signal(query, matches)
        tasks.append(
            {
                "query_atom_id": unit["unit_id"],
                "signal": signal,
                "signal_context": _signal_context(graph, signal) if signal else "",
                "retrieval_method": "semantic_dense_signal_relation",
                "semantic_query": {
                    "atom_id": query.get("atom_id", ""),
                    "kind": query.get("kind", ""),
                    "source_refs": query.get("source_refs", []),
                    "source_file": query.get("source_file", ""),
                    "line_start": query.get("line_start"),
                    "line_end": query.get("line_end"),
                },
                "assumptions": [
                    {
                        "spec_id": query.get("spec_id", ""),
                        "constraint": query.get("text", ""),
                        "bug_relevance": (
                            "[UNCERTAIN — LLM flagged potential issue]"
                            if query.get("kind") == "uncertain"
                            else ""
                        ),
                        "related_signals": query.get("signals", []),
                        "source_kind": query.get("kind", ""),
                    }
                ],
                "guarantees": [
                    {
                        "spec_id": match.get("spec_id", ""),
                        "property": match.get("text", ""),
                        "output_signals": match.get("signals", []),
                        "rank": match.get("rank"),
                        "score": match.get("score"),
                        "pair_type": match.get("pair_type"),
                        "shared_signals": match.get("shared_signals", []),
                    }
                    for match in matches
                ],
            }
        )

    batch_prompt = (
        prompt_template
        + "\n\nBatch-mode guardrails:\n"
        + "You will receive multiple independent semantic AG checking tasks. "
        + "Evaluate every task independently using the same rules above. "
        + "Do not infer a mismatch from neighboring batch items unless they "
        + "explicitly share signals, source, guarantees, or a control/data path. "
        + "If batch items are unrelated, say so implicitly by leaving "
        + "cross_item_context_used=false. "
        + "Return only valid JSON in this exact shape: "
        + '{"results":[{"query_atom_id":"...","findings":[{...}],'
        + '"cross_item_context_used":false,"cross_item_context_ids":[]}]} '
        + "Each finding object must use the normal Channel B finding schema "
        + "from the prompt. If a task has no bug-relevant finding, use an empty "
        + "findings array for that query_atom_id."
    )
    context = {
        "batch_id": batch["batch_id"],
        "batch_guardrails": {
            "query_count": batch["query_count"],
            "pair_count": batch["pair_count"],
            "signal_roots": batch.get("signal_roots", []),
            "dense_fallback_uncertain_count": batch.get(
                "dense_fallback_uncertain_count", 0
            ),
        },
        "tasks": tasks,
    }
    content = client.chat(
        messages=[
            {"role": "system", "content": batch_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=2)},
        ],
        max_tokens=max_tokens,
    )
    parsed = _parse_llm_response(content)
    return _flatten_semantic_batch_findings(parsed, unit_by_id, batch["batch_id"])


def _flatten_semantic_batch_findings(
    parsed: dict[str, Any],
    unit_by_id: dict[str, dict[str, Any]],
    batch_id: int,
) -> list[dict[str, Any]]:
    """Normalize batch JSON into the same flat finding list used elsewhere."""
    out: list[dict[str, Any]] = []
    if isinstance(parsed.get("results"), list):
        for result in parsed["results"]:
            if not isinstance(result, dict):
                continue
            uid = str(result.get("query_atom_id", ""))
            unit = unit_by_id.get(uid)
            findings = result.get("findings", [])
            if isinstance(findings, dict):
                findings = [findings]
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if isinstance(finding, dict):
                    out.append(
                        _annotate_semantic_finding(
                            finding,
                            uid,
                            unit,
                            batch_id,
                            result.get("cross_item_context_used", False),
                            result.get("cross_item_context_ids", []),
                        )
                    )
        return out

    if isinstance(parsed.get("findings"), list):
        for finding in parsed["findings"]:
            if not isinstance(finding, dict):
                continue
            uid = str(
                finding.get("query_atom_id")
                or finding.get("_semantic_unit")
                or finding.get("semantic_query", {}).get("atom_id", "")
            )
            out.append(
                _annotate_semantic_finding(
                    finding,
                    uid,
                    unit_by_id.get(uid),
                    batch_id,
                    finding.get("cross_item_context_used", False),
                    finding.get("cross_item_context_ids", []),
                )
            )
    return out


def _annotate_semantic_finding(
    finding: dict[str, Any],
    uid: str,
    unit: dict[str, Any] | None,
    batch_id: int,
    cross_used: Any,
    cross_ids: Any,
) -> dict[str, Any]:
    item = dict(finding)
    if unit is not None:
        query = unit["query"]
        matches = unit["matches"]
        item.setdefault("signal", _semantic_unit_signal(query, matches))
        item.setdefault(
            "assumption",
            {
                "spec_id": query.get("spec_id", ""),
                "constraint": query.get("text", ""),
            },
        )
        item.setdefault(
            "relevant_guarantees",
            [
                {
                    "spec_id": match.get("spec_id", ""),
                    "property": match.get("text", ""),
                }
                for match in matches
            ],
        )
        item["_semantic_query_kind"] = query.get("kind", "")
    item["_semantic_unit"] = uid
    item["_semantic_batch"] = batch_id
    item["cross_item_context_used"] = bool(cross_used)
    item["cross_item_context_ids"] = cross_ids if isinstance(cross_ids, list) else []
    return item


def _semantic_unit_signal(query: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    for match in matches:
        shared = match.get("shared_signals") or []
        if shared:
            return str(shared[0])
    signals = query.get("signals") or []
    if signals:
        return str(signals[0])
    for match in matches:
        signals = match.get("signals") or []
        if signals:
            return str(signals[0])
    return "semantic_ag"


def _signal_context(graph: SignalGraph, signal: str) -> str:
    """Build a concise text snippet describing *signal*'s role."""
    info = graph.signals.get(signal)
    if not info:
        return ""

    parts: list[str] = []
    parts.append(f"Signal kind: {info.kind}")
    if info.context_snippets:
        parts.append("Context excerpts from specs:")
        for snip in info.context_snippets[:3]:
            parts.append(f"  {snip[:200]}")
    return "\n".join(parts)


def _parse_llm_response(content: str) -> dict[str, Any]:
    """Parse the LLM JSON response, handling markdown wrapping."""
    import re

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
