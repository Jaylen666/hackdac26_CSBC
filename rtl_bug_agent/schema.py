from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RtlChunk:
    chunk_id: str
    kind: str
    source_file: str
    module: str | None
    line_start: int
    line_end: int
    title: str
    context_summary: str
    code: str
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

