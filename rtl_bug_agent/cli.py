from __future__ import annotations

import argparse
import json
from pathlib import Path

from rtl_bug_agent.env import get_provider_config, load_dotenv, make_client
from rtl_bug_agent.llm.client import LlmConfig, OpenAICompatibleClient
from rtl_bug_agent.rtl.chunker import (
    chunk_sv_files,
    chunk_sv_files_structural_aware,
)
from rtl_bug_agent.rtl.io import get_chunk, read_chunks, write_chunks
from rtl_bug_agent.spec.extractor import generate_chunk_spec
from rtl_bug_agent.phase2.structural_facts import (
    extract_structural_facts,
    summarise_structural_facts,
    write_structural_facts,
)
from rtl_bug_agent.phase2.semantic_ag import (
    SemanticBatchConfig,
    SemanticAgConfig,
    build_pairing as build_semantic_pairing,
    summarise_pairing,
)
from rtl_bug_agent.phase2.signal_graph import build_signal_graph


def main() -> None:
    parser = argparse.ArgumentParser(prog="rtl_bug_agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chunk = sub.add_parser("chunk", help="Chunk RTL files into semantic units")
    p_chunk.add_argument("--rtl-dir", required=True)
    p_chunk.add_argument("--out", required=True)
    p_chunk.add_argument("--prefilter", action="store_true",
                          help="Use LLM to skip structural template sections")
    p_chunk.add_argument(
        "--structural-aware",
        action="store_true",
        help="Split structural facts from behavior chunks and emit optional side outputs",
    )
    p_chunk.add_argument("--manifest-out", help="Optional chunk manifest JSON path")
    p_chunk.add_argument("--structure-facts-out", help="Optional structural facts JSONL path")

    p_show = sub.add_parser("show", help="Show one chunk")
    p_show.add_argument("--chunks", required=True)
    p_show.add_argument("--chunk-id")
    p_show.add_argument("--index", type=int)

    p_spec = sub.add_parser("spec", help="Generate spec for one selected chunk")
    p_spec.add_argument("--chunks", required=True)
    p_spec.add_argument("--chunk-id", required=True)
    p_spec.add_argument("--out", required=True)
    p_spec.add_argument("--env", default="/home/smy/.env")
    p_spec.add_argument("--provider", default="GUOCHUANG")

    p_sem = sub.add_parser("semantic-ag", help="Dry-run semantic A-G retrieval")
    p_sem.add_argument("--specs-dir", required=True)
    p_sem.add_argument("--out", required=True)
    p_sem.add_argument("--cache-dir", default="output/.semantic_ag_cache")
    p_sem.add_argument("--model", default="BAAI/bge-m3")
    p_sem.add_argument("--hf-home",
                       default="/home/smy/rtl_bug_agent/experiments/bge_m3_ag_retrieval/out/hf_cache",
                       help="Local HuggingFace cache for BGE-M3; used offline by default")
    p_sem.add_argument("--online-download-model", action="store_true",
                       help="Allow HuggingFace network access to download missing model files")
    p_sem.add_argument("--batch-size", type=int, default=16)
    p_sem.add_argument("--embeddings",
                       help="Optional precomputed .npz embeddings for offline dry-run")
    p_sem.add_argument("--semantic-batch-mode", choices=["single", "guarded"],
                       default="single")
    p_sem.add_argument("--semantic-max-queries-per-batch", type=int, default=5)
    p_sem.add_argument("--semantic-max-prompt-tokens", type=int, default=5500)
    p_sem.add_argument("--semantic-max-dense-fallback-uncertain", type=int, default=1)
    p_sem.add_argument("--semantic-min-shared-roots", type=int, default=1)
    p_sem.add_argument("--semantic-max-signal-roots", type=int, default=4)
    p_sem.add_argument("--structural-facts",
                       help="Optional JSONL/JSON file with precomputed structural facts")
    p_sem.add_argument("--out-root",
                       help="Optional root directory for semantic artifacts")

    p_struct = sub.add_parser("structural-facts", help="Extract structural facts from RTL files")
    p_struct.add_argument("--rtl-files", nargs="+", required=True)
    p_struct.add_argument("--out", required=True)

    args = parser.parse_args()
    if args.cmd == "chunk":
        client = None
        if args.prefilter:
            load_dotenv()
            client = make_client("GUOCHUANG_DEEPSEEK")
            print("Pre-filter LLM enabled (GUOCHUANG_DEEPSEEK)")
        if args.structural_aware:
            chunks, manifest, facts = chunk_sv_files_structural_aware(args.rtl_dir, client=client)
            if args.manifest_out:
                Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.manifest_out).write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if args.structure_facts_out:
                from rtl_bug_agent.phase2.structural_facts import write_structural_facts
                Path(args.structure_facts_out).parent.mkdir(parents=True, exist_ok=True)
                write_structural_facts(facts, args.structure_facts_out)
            print(
                f"Structural-aware chunking: {len(chunks)} behavior chunks, "
                f"{len(manifest)} manifest records, {len(facts)} structure facts"
            )
        else:
            chunks = chunk_sv_files(args.rtl_dir, client=client)
        write_chunks(chunks, args.out)
        by_kind: dict[str, int] = {}
        for chunk in chunks:
            by_kind[chunk.kind] = by_kind.get(chunk.kind, 0) + 1
        print(f"wrote {len(chunks)} chunks to {args.out}")
        print(json.dumps(by_kind, ensure_ascii=False, indent=2))
        for idx, chunk in enumerate(chunks[:20]):
            print(f"{idx:03d} {chunk.chunk_id} {chunk.source_file}:{chunk.line_start}-{chunk.line_end}")
        if len(chunks) > 20:
            print(f"... {len(chunks) - 20} more chunks")
        return

    if args.cmd == "show":
        chunks = read_chunks(args.chunks)
        if args.chunk_id:
            chunk = get_chunk(chunks, args.chunk_id)
        elif args.index is not None:
            chunk = chunks[args.index]
        else:
            raise SystemExit("show requires --chunk-id or --index")
        print(json.dumps(chunk.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.cmd == "spec":
        load_dotenv(args.env)
        api_key, base_url, model = get_provider_config(args.provider)
        chunks = read_chunks(args.chunks)
        chunk = get_chunk(chunks, args.chunk_id)
        client = OpenAICompatibleClient(LlmConfig(api_key=api_key, base_url=base_url, model=model))
        spec = generate_chunk_spec(chunk, client)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote spec for {chunk.chunk_id} to {args.out}")
        return

    if args.cmd == "semantic-ag":
        graph = build_signal_graph(args.specs_dir, structural_facts_path=args.structural_facts)
        cfg = SemanticAgConfig(
            model_name=args.model,
            hf_home=args.hf_home,
            offline=not args.online_download_model,
            batch_size=args.batch_size,
        )
        batch_cfg = SemanticBatchConfig(
            mode=args.semantic_batch_mode,
            max_queries=args.semantic_max_queries_per_batch,
            max_prompt_tokens=args.semantic_max_prompt_tokens,
            max_dense_fallback_uncertain=args.semantic_max_dense_fallback_uncertain,
            min_shared_roots=args.semantic_min_shared_roots,
            max_signal_roots=args.semantic_max_signal_roots,
        )
        pairing = build_semantic_pairing(
            graph,
            cache_dir=args.cache_dir,
            config=cfg,
            embeddings_path=args.embeddings,
        )
        summary = summarise_pairing(pairing, batch_cfg)
        out_path = Path(args.out)
        if args.out_root:
            out_path = Path(args.out_root) / out_path.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(pairing, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"wrote semantic AG pairing to {args.out}")
        return

    if args.cmd == "structural-facts":
        facts = extract_structural_facts(args.rtl_files)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_structural_facts(facts, out_path)
        summary = summarise_structural_facts(facts)
        print(json.dumps({
            "num_facts": summary.num_facts,
            "num_files": summary.num_files,
            "by_kind": summary.by_kind,
            "num_signals_indexed": summary.num_signals_indexed,
            "num_ranked_facts": summary.num_ranked_facts,
        }, ensure_ascii=False, indent=2))
        print(f"wrote structural facts to {args.out}")
        return


if __name__ == "__main__":
    main()
