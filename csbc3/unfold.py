"""
Generate block unfolder.

Static elaboration: unrolls `generate for (i=0; i<N; i++)` blocks into
N copies of their body, substituting the genvar with the iteration value.

After unfolding, each result is classified as assign / always / instance
and passed to the appropriate pipeline path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from csbc3.chunker import Chunk, chunk_file


def unfold_generate(code: str) -> str:
    """Unfold static generate for-loops in Verilog code.

    Detects patterns like:
      generate
        for (genvar i=0; i<N; i++) begin : label
          ... body using i ...
        end
      endgenerate

    And replaces with N copies of body, i → 0, 1, ..., N-1.
    """
    # Pattern: generate ... for (genvar i=START; i<END; i++) begin : label
    # We handle simple cases: genvar, constant bounds, single loop variable
    gen_pattern = re.compile(
        r"generate\s*\n"
        r"(.*?)"
        r"for\s*\(\s*genvar\s+(\w+)\s*=\s*(\d+)\s*;\s*\2\s*<\s*(\d+)\s*;\s*\2\s*\+\+\s*\)"
        r"\s*begin\s*:\s*(\w+)"
        r"(.*?)"
        r"end\s*\n"
        r"(.*?)"
        r"endgenerate",
        re.DOTALL,
    )

    def _replace(m: re.Match) -> str:
        preamble = m.group(1)
        var = m.group(2)
        start = int(m.group(3))
        end = int(m.group(4))
        label = m.group(5)
        body = m.group(6)
        postamble = m.group(7)

        # Also handle : label begin syntax
        body = re.sub(r"^\s*begin\s*:\s*\w+\s*", "", body)

        copies = []
        for i in range(start, end):
            # Substitute genvar reference with iteration value
            subbed = re.sub(rf"\b{var}\b", str(i), body)
            copies.append(f"// {label}[{i}]\n{subbed}")

        result = preamble + "\n".join(copies) + postamble
        result = re.sub(r"generate\s*$", "", result, flags=re.MULTILINE)
        return result

    result = gen_pattern.sub(_replace, code)
    return result


def unfold_file(path: str | Path, module_name: str | None = None) -> list[Chunk]:
    """Read a file, unfold generates, then chunk the result."""
    original = Path(path).read_text(encoding="utf-8")
    unfolded = unfold_generate(original)

    # Write unfolded to a temp file for chunking
    tmp = Path("/tmp") / f"_unfolded_{Path(path).name}"
    tmp.write_text(unfolded, encoding="utf-8")
    chunks = chunk_file(str(tmp), module_name=module_name)
    tmp.unlink(missing_ok=True)

    return chunks


def unfold_and_classify(path: str | Path, module_name: str | None = None) -> dict[str, list[Chunk]]:
    """Unfold and classify chunks by construct type."""
    chunks = unfold_file(path, module_name)
    result: dict[str, list[Chunk]] = {
        "assign": [],
        "always_comb": [],
        "always_ff": [],
        "instance": [],
        "generate": [],
        "unsupported": [],
    }
    for c in chunks:
        ct = c.construct_type
        if ct in result:
            result[ct].append(c)
        else:
            result["unsupported"].append(c)
    return result
