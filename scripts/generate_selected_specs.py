from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rtl_bug_agent.env import get_provider_config, load_dotenv
from rtl_bug_agent.llm.client import LlmConfig, OpenAICompatibleClient
from rtl_bug_agent.rtl.io import get_chunk, read_chunks
from rtl_bug_agent.spec.extractor import generate_chunk_spec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--env", default="/home/smy/.env")
    parser.add_argument("--provider", default="GUOCHUANG")
    parser.add_argument(
        "--prompt",
        default=None,
        help="Optional spec prompt path; defaults to the extractor's current prompt",
    )
    parser.add_argument("chunk_ids", nargs="+")
    args = parser.parse_args()

    load_dotenv(args.env)
    api_key, base_url, model = get_provider_config(args.provider)
    client = OpenAICompatibleClient(LlmConfig(api_key=api_key, base_url=base_url, model=model))
    chunks = read_chunks(args.chunks)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for chunk_id in args.chunk_ids:
        chunk = get_chunk(chunks, chunk_id)
        spec = generate_chunk_spec(chunk, client, prompt_path=args.prompt or None)
        out_path = out_dir / f"{chunk_id}.json"
        out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
