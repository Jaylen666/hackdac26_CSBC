from __future__ import annotations

import json
from pathlib import Path

from rtl_bug_agent.schema import RtlChunk


def write_chunks(chunks: list[RtlChunk], path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([chunk.to_dict() for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_chunks(path: str | Path) -> list[RtlChunk]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RtlChunk(**item) for item in raw]


def get_chunk(chunks: list[RtlChunk], chunk_id: str) -> RtlChunk:
    for chunk in chunks:
        if chunk.chunk_id == chunk_id:
            return chunk
    raise KeyError(f"Unknown chunk_id: {chunk_id}")

