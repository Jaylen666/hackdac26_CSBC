"""Generate specs for ALL chunks in a chunk file, batching small isolated
continuous_regions into grouped LLM calls to reduce API overhead.

Usage:

    python3 scripts/generate_all_specs.py \\
      --chunks output/hmac_chunks.json \\
      --out-dir output/specs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.env import load_dotenv, make_client
from rtl_bug_agent.rtl.io import read_chunks
from rtl_bug_agent.schema import RtlChunk
from rtl_bug_agent.spec.extractor import generate_chunk_spec

# Chunks of kind ``continuous_region`` with fewer than this many lines are
# considered "isolated fragments" and will be batched per source file.
SMALL_THRESHOLD = 5


def _separate_chunks(
    chunks: list[RtlChunk], threshold: int = SMALL_THRESHOLD
) -> tuple[list[RtlChunk], list[RtlChunk]]:
    """Split chunks into *normal* (one spec per chunk) and *batched*
    (synthetic chunks representing grouped small fragments)."""
    normal: list[RtlChunk] = []
    small_by_file: dict[str, list[RtlChunk]] = defaultdict(list)

    for c in chunks:
        if c.kind == "continuous_region" and (c.line_end - c.line_start + 1) < threshold:
            small_by_file[c.source_file].append(c)
        else:
            normal.append(c)

    batched: list[RtlChunk] = []
    for source_file, small_list in small_by_file.items():
        small_list.sort(key=lambda c: c.line_start)
        batched.append(_make_batched_chunk(small_list, source_file))

    return normal, batched


def _make_batched_chunk(
    small_chunks: list[RtlChunk], source_file: str
) -> RtlChunk:
    """Build a synthetic RtlChunk from a batch of small isolated fragments.

    Code from each fragment is concatenated with ``// ---`` separators that
    carry the original line range so the LLM can reference specific locations.
    """
    short_fn = Path(source_file).stem
    code_parts: list[str] = []
    for c in small_chunks:
        code_parts.append(f"// --- {short_fn}.sv:{c.line_start}-{c.line_end} ---")
        code_parts.append(c.code)

    first = small_chunks[0]
    last = small_chunks[-1]

    # Use the module from the chunk that has one; None is fine for file-scope
    module = first.module
    scope = module or short_fn
    safe_scope = scope.replace(".", "_")

    return RtlChunk(
        chunk_id=f"{safe_scope}__continuous_region__batched_small_assigns__001",
        kind="continuous_region",
        source_file=source_file,
        module=module,
        line_start=first.line_start,
        line_end=last.line_end,
        title=(
            f"{scope}: batched {len(small_chunks)} isolated small "
            f"declarations / assigns"
        ),
        context_summary=(
            f"{scope} 中 {len(small_chunks)} 个孤立的小型声明/赋值语句"
            f"（被 always 块或其他语义边界间隔，无法合并为连续区域），"
            f"跨行 {first.line_start}-{last.line_end}。"
        ),
        code="\n".join(code_parts),
        dependencies=sorted({
            d for c in small_chunks for d in (c.dependencies or [])
        })[:40],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate specs for all chunks, batching small fragments"
    )
    parser.add_argument("--chunks", required=True, help="Path to chunks JSON file")
    parser.add_argument("--out-dir", required=True, help="Output directory for spec JSONs")
    parser.add_argument("--env", default="/home/smy/.env", help="Path to .env file")
    parser.add_argument("--provider", default="GUOCHUANG_DEEPSEEK", help="Env var prefix for LLM config")
    parser.add_argument(
        "--small-threshold", type=int, default=SMALL_THRESHOLD,
        help=f"Max lines for a continuous_region to be batched (default: {SMALL_THRESHOLD})",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds between LLM calls to avoid rate-limiting (default: 0.3)",
    )
    args = parser.parse_args()

    # --- Load config ----------------------------------------------------------
    load_dotenv(args.env)
    client = make_client(args.provider, thinking="medium")

    # --- Organise chunks ------------------------------------------------------
    chunks = read_chunks(args.chunks)
    normal, batched = _separate_chunks(chunks, args.small_threshold)

    small_count = sum(
        1 for c in chunks
        if c.kind == "continuous_region"
        and (c.line_end - c.line_start + 1) < args.small_threshold
    )
    total_calls = len(normal) + len(batched)

    print(f"Chunks: {len(chunks)} total")
    print(f"  Individual specs:     {len(normal)}")
    print(f"  Batched groups:       {len(batched)}  (from {small_count} small fragments)")
    print(f"  Total LLM calls:      {total_calls}")
    print()

    # --- Generate specs -------------------------------------------------------
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_targets = normal + batched
    ok_count = 0
    skip_count = 0
    fail_count = 0

    for i, chunk in enumerate(all_targets):
        out_path = out_dir / f"{chunk.chunk_id}.json"

        if out_path.exists():
            print(f"[{i+1:3d}/{total_calls}] SKIP  {chunk.chunk_id}  (already exists)")
            skip_count += 1
            continue

        print(
            f"[{i+1:3d}/{total_calls}] GEN   {chunk.chunk_id}  "
            f"({chunk.line_end - chunk.line_start + 1}L) ...",
            end=" ", flush=True,
        )
        try:
            spec = generate_chunk_spec(chunk, client)
            out_path.write_text(
                json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("OK")
            ok_count += 1
        except Exception as exc:
            print(f"FAIL  ({exc})")
            out_path.write_text(
                json.dumps(
                    {"chunk_id": chunk.chunk_id, "error": str(exc)},
                    ensure_ascii=False, indent=2,
                )
            )
            fail_count += 1

        # Polite delay to avoid rate-limiting
        if args.delay > 0:
            time.sleep(args.delay)

    # --- Summary --------------------------------------------------------------
    print()
    print(f"Done.  OK: {ok_count}  Skipped: {skip_count}  Failed: {fail_count}")
    print(f"Specs written to {out_dir}")
    s = client.stats()
    print(f"  LLM calls: {s['call_count']}  Tokens: {s['total_tokens']:,} "
          f"({s['total_input_tokens']:,} in / {s['total_output_tokens']:,} out)  "
          f"Wall: {s['total_wall_seconds']:.0f}s")
    import json as _json
    (Path(args.out_dir) / "_stats.json").write_text(
        _json.dumps({"phase": "spec_generation", **s}, ensure_ascii=False, indent=2),
        encoding="utf-8")


if __name__ == "__main__":
    main()
