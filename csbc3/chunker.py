"""
Chunker: decompose SystemVerilog files into construct-level chunks.

Each chunk maps to exactly one construct in the formal syntax:
  - AssignChunk:    one ``assign X = expr;`` statement
  - AlwaysChunk:    one ``always_comb`` / ``always_ff`` block
  - InstanceChunk:  one module instantiation
  - GenerateChunk:  one ``generate`` / ``for`` / ``if`` block (covered poorly by formal syntax → NL-only)
  - UnsupportedChunk: anything else (packages, interfaces, primitives → NL-only)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Chunk:
    chunk_id: str
    construct_type: str  # assign | always_comb | always_ff | instance | generate | unsupported
    source_file: str
    line_start: int
    line_end: int
    # The actual RTL code text
    code: str
    # Signals this chunk drives (outputs / LHS of assignments)
    driven_signals: list[str] = field(default_factory=list)
    # Signals this chunk reads (inputs / RHS references)
    read_signals: list[str] = field(default_factory=list)
    # For instances: the submodule name and instance name
    submodule: str = ""
    instance_name: str = ""
    # Port connections: [(port_name, connected_signal), ...]
    port_connections: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "construct_type": self.construct_type,
            "source_file": self.source_file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "code": self.code,
            "driven_signals": self.driven_signals,
            "read_signals": self.read_signals,
            "submodule": self.submodule,
            "instance_name": self.instance_name,
            "port_connections": self.port_connections,
        }


# ---------------------------------------------------------------------------
# Chunking logic: scan file line by line, identify construct boundaries
# ---------------------------------------------------------------------------

# Keywords that start a new construct at top level
_CONSTRUCT_STARTS = re.compile(
    r"^\s*(assign\s|always_comb\b|always_ff\b|always_latch\b|"
    r"generate\b|endgenerate\b|module\b|endmodule\b|"
    r"interface\b|endinterface\b|package\b|endpackage\b|"
    r"typedef\b|enum\b)"
)

# Pattern to match the start of a module instantiation:
#   <identifier> #( ... )? <identifier> ( ... ) ;
# Simple heuristic: a line with "#(" or "(" after an identifier, ending with ");"
_INSTANCE_START = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\(.*\)\s*)?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(.*$")

# Pattern to match assign statement:
_ASSIGN_RE = re.compile(r"^\s*assign\s+(\S+)\s*=(.*);$")

# Pattern to extract port connections from a line like: .port_name (signal_name),
_PORT_CONN = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_.[\]]*)\s*\)\s*,?\s*")


def chunk_file(file_path: str | Path, module_name: str | None = None) -> list[Chunk]:
    """Decompose a SystemVerilog file into construct-level chunks."""
    src = Path(file_path)
    lines = src.read_text(encoding="utf-8").splitlines()
    file_name = src.name
    chunks: list[Chunk] = []

    in_module = (module_name is not None)
    module_depth = 0  # tracks nesting inside module (for endmodule matching)
    paren_depth = 0   # tracks parens for module port list

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not in_module:
            if _is_module_header(stripped):
                in_module = True
                module_depth = 1
                # Find the closing paren of the port list
                for ch in line:
                    if ch == "(": paren_depth += 1
                    elif ch == ")": paren_depth -= 1
                i += 1
                continue
            i += 1
            continue

        # Track parens for module port list spanning multiple lines
        for ch in line:
            if ch == "(": paren_depth += 1
            elif ch == ")": paren_depth -= 1

        if stripped.startswith("endmodule") and paren_depth <= 0:
            in_module = False
            module_depth = 0
            i += 1
            continue

        # Skip the module header (still in the port list)
        if paren_depth > 0:
            i += 1
            continue

        # Inside a module at top level — find constructs
        # (assign/always/instance are always top-level in a well-formed module)

        # --- assign statement ---
        assign_m = _ASSIGN_RE.match(stripped)
        if assign_m:
            lhs = assign_m.group(1)
            start_i = i
            end_i = _find_statement_end(lines, i)
            code = "\n".join(lines[start_i:end_i + 1])
            chunks.append(Chunk(
                chunk_id=f"{module_name or file_name}__assign__l{start_i + 1}",
                construct_type="assign",
                source_file=str(src),
                line_start=i + 1,
                line_end=end_i + 1,
                code=code,
                driven_signals=[lhs],
                read_signals=_extract_read_signals(code),
            ))
            i = end_i + 1
            continue

        # --- module instance ---
        inst_m = _INSTANCE_START.match(stripped)
        if inst_m and not stripped.startswith("assign") and not _is_always_start(stripped):
            submod = inst_m.group(1)
            inst_name = inst_m.group(2)
            start_i = i
            end_i = _find_paren_block_end(lines, i)
            if end_i > i:
                code = "\n".join(lines[start_i:end_i + 1])
                ports = _PORT_CONN.findall(code)
                conn_signals = [p[1] for p in ports]
                chunks.append(Chunk(
                    chunk_id=f"{module_name or file_name}__inst__{inst_name}",
                    construct_type="instance",
                    source_file=str(src),
                    line_start=start_i + 1,
                    line_end=end_i + 1,
                    code=code,
                    submodule=submod,
                    instance_name=inst_name,
                    port_connections=ports,
                    read_signals=conn_signals,
                ))
                i = end_i + 1
                continue

        # --- always_comb / always_ff ---
        if _is_always_start(stripped):
            end_i = _find_block_end(lines, i)
            code = "\n".join(lines[i:end_i + 1])
            kind = "always_comb" if "always_comb" in stripped else "always_ff"
            # Extract driven signals from assignments within the block
            driven = _extract_assign_targets(code)
            read = _extract_read_signals(code)
            chunks.append(Chunk(
                chunk_id=f"{module_name or file_name}__{kind}__l{i+1}",
                construct_type=kind,
                source_file=str(src),
                line_start=i + 1,
                line_end=end_i + 1,
                code=code,
                driven_signals=driven,
                read_signals=read,
            ))
            i = end_i + 1
            continue

        # --- generate block ---
        if stripped.startswith("generate"):
            end_i = _find_endgenerate(lines, i)
            code = "\n".join(lines[i:end_i + 1])
            chunks.append(Chunk(
                chunk_id=f"{module_name or file_name}__generate__l{i+1}",
                construct_type="generate",
                source_file=str(src),
                line_start=i + 1,
                line_end=end_i + 1,
                code=code,
            ))
            i = end_i + 1
            continue

        i += 1

    return chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_module_header(stripped: str) -> bool:
    return stripped.startswith("module ")

def _is_always_start(stripped: str) -> bool:
    return stripped.startswith("always_comb") or stripped.startswith("always_ff") \
           or stripped.startswith("always_latch")

def _find_statement_end(lines: list[str], start: int) -> int:
    """Find the end of a statement starting at line *start* (looking for semicolon)."""
    for i in range(start, min(start + 10, len(lines))):
        if ";" in lines[i]:
            return i
    return start

def _find_block_end(lines: list[str], start: int) -> int:
    """Find the end of a begin..end block starting at line *start*."""
    depth = 0
    found_begin = False
    in_paren = 0
    for i in range(start, len(lines)):
        line = lines[i]
        # Track begin/end
        if "begin" in line:
            depth += 1
            found_begin = True
        if "end" in line:
            depth -= 1
        if found_begin and depth <= 0:
            return i
    # No begin/end found — might be a single statement block
    for i in range(start, min(start + 10, len(lines))):
        if ";" in lines[i]:
            return i
    return len(lines) - 1

def _find_paren_block_end(lines: list[str], start: int) -> int:
    """Find the end of a parenthesized block (...), handling nesting."""
    depth = 0
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if depth == 0 and ";" in lines[i]:
            return i
    return start

def _find_endgenerate(lines: list[str], start: int) -> int:
    for i in range(start, len(lines)):
        if lines[i].strip() == "endgenerate":
            return i
    return len(lines) - 1

def _update_depth(stripped: str, current: int) -> None:
    pass  # handled by _depth_delta

def _depth_delta(stripped: str) -> int:
    d = 0
    for word in stripped.split():
        if word == "begin" or word.endswith(":") and not word.endswith("\\"):
            d += 1
        if word.strip(";") == "end":
            d -= 1
    return d

def _extract_assign_targets(code: str) -> list[str]:
    """Extract all LHS signal names from assignment statements in procedural code."""
    targets: list[str] = []
    # Match: signal = ..., signal <= ...
    for m in re.finditer(r"^\s*(\S+)\s*(?:=|<=)", code, re.MULTILINE):
        sig = m.group(1).strip()
        if sig not in targets:
            targets.append(sig)
    return targets

def _extract_read_signals(code: str) -> list[str]:
    """Extract likely signal names from RHS of assignments and conditions."""
    # Simple heuristic: find all identifiers that aren't SV keywords
    names = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", code))
    sv_kw = {"assign", "always", "always_comb", "always_ff", "if", "else", "case",
             "endcase", "begin", "end", "for", "generate", "endgenerate",
             "module", "endmodule", "input", "output", "inout", "wire", "reg",
             "logic", "posedge", "negedge", "or", "and", "not", "unique", "priority"}
    return [n for n in names if n not in sv_kw and not n.isdigit()][:50]


def chunk_directory(rtl_dir: str) -> dict[str, list[Chunk]]:
    """Chunk all .sv files in a directory, grouped by file."""
    result: dict[str, list[Chunk]] = {}
    for sv_path in sorted(Path(rtl_dir).glob("*.sv")):
        try:
            chunks = chunk_file(sv_path)
            if chunks:
                result[str(sv_path)] = chunks
        except Exception as e:
            print(f"  Error chunking {sv_path}: {e}")
    return result
