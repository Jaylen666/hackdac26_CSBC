from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_SIGNAL_LIKE_SUFFIXES = (
    "_we",
    "_re",
    "_sel",
    "_qe",
    "_q",
    "_d",
    "_de",
    "_weq",
    "_valid",
    "_ready",
    "_err",
    "_error",
    "_en",
)


@dataclass(frozen=True)
class StructuralFactSummary:
    num_facts: int
    num_files: int
    by_kind: dict[str, int]
    num_signals_indexed: int
    num_ranked_facts: int


def is_structural_sv_file(path: str | Path) -> bool:
    name = Path(path).name.lower()
    return bool(
        re.search(r"_reg_top\.sv$", name)
        or re.search(r"_reg_pkg\.sv$", name)
        or re.search(r"_pkg\.sv$", name)
        or re.search(r"_reg\.sv$", name)
    )


def extract_structural_facts(files: list[str | Path]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for file_path in files:
        path = Path(file_path)
        if not path.exists():
            continue
        facts.extend(_extract_file(path))
    facts.sort(
        key=lambda item: (
            item.get("source_file", ""),
            item.get("line_start", 0),
            item.get("kind", ""),
            item.get("fact_id", ""),
        )
    )
    return facts


def write_structural_facts(facts: list[dict[str, Any]], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(fact, ensure_ascii=False, sort_keys=True) for fact in facts)
        + ("\n" if facts else ""),
        encoding="utf-8",
    )


def load_structural_facts(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        raw = json.loads(text)
        return raw if isinstance(raw, list) else []
    facts: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            facts.append(json.loads(line))
    return facts


def compact_structural_fact(fact: dict[str, Any], signal_roots: list[str] | None = None) -> dict[str, Any]:
    """Return a prompt-safe compact representation of a structural fact."""
    roots = {
        normalize_structural_signal(sig)
        for sig in (signal_roots or [])
        if normalize_structural_signal(sig)
    }
    signals = [str(sig) for sig in fact.get("signals", []) or []]
    matched = []
    if roots:
        for sig in signals:
            if normalize_structural_signal(sig) in roots:
                matched.append(sig)
    else:
        matched = signals[:]

    out = {
        "fact_id": fact.get("fact_id", ""),
        "kind": fact.get("kind", ""),
        "name": fact.get("name", ""),
        "signals": matched[:8],
        "rank_score": fact.get("rank_score", 0),
        "source_file": fact.get("source_file", ""),
        "line_start": fact.get("line_start", 0),
        "line_end": fact.get("line_end", 0),
    }
    if fact.get("kind") == "assign_guard":
        out["lhs"] = fact.get("name", "")
        out["rhs"] = _compact_rhs(str(fact.get("expression", "")))
        if fact.get("guard_signal"):
            out["guard_signal"] = fact.get("guard_signal")
        if fact.get("guard_polarity"):
            out["guard_polarity"] = fact.get("guard_polarity")
    elif fact.get("kind") == "instance_map":
        out["module_name"] = fact.get("module_name", "")
        out["instance_name"] = fact.get("instance_name", "")
        ports = fact.get("port_map", {}) or {}
        out["ports"] = {
            k: _compact_rhs(str(v))
            for k, v in list(ports.items())[:8]
            if not roots or any(normalize_structural_signal(tok) in roots for tok in _expr_signals(str(v)))
        }
        params = fact.get("param_map", {}) or {}
        out["params"] = {
            k: _compact_rhs(str(v))
            for k, v in list(params.items())[:8]
        }
    elif fact.get("kind") in {"param_def", "enum_def", "struct_def"}:
        out["expression"] = _compact_rhs(str(fact.get("expression", "")))
    else:
        out["expression"] = _compact_rhs(str(fact.get("expression", "")))
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _compact_rhs(expr: str, max_terms: int = 12) -> str:
    expr = re.sub(r"\s+", " ", expr).strip()
    if not expr:
        return expr
    if len(expr) <= 160:
        return expr
    terms = _IDENT_RE.findall(expr)
    if terms:
        clipped = " ".join(dict.fromkeys(terms[:max_terms]))
        return clipped if clipped else expr[:160]
    return expr[:160]


def normalize_structural_signal(name: str) -> str:
    sig = name.strip()
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
    return sig


def index_structural_facts(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        for sig in fact.get("signals", []) or []:
            norm = normalize_structural_signal(str(sig))
            if not norm:
                continue
            index.setdefault(norm, []).append(fact)
    return index


def summarise_structural_facts(facts: list[dict[str, Any]]) -> StructuralFactSummary:
    by_kind: dict[str, int] = {}
    by_file: set[str] = set()
    num_signals = 0
    num_ranked = 0
    for fact in facts:
        by_kind[fact.get("kind", "")] = by_kind.get(fact.get("kind", ""), 0) + 1
        if fact.get("source_file"):
            by_file.add(str(fact["source_file"]))
        num_signals += len(fact.get("signals", []) or [])
        if int(fact.get("rank_score", 0) or 0) > 0:
            num_ranked += 1
    return StructuralFactSummary(
        num_facts=len(facts),
        num_files=len(by_file),
        by_kind=by_kind,
        num_signals_indexed=num_signals,
        num_ranked_facts=num_ranked,
    )


def _extract_file(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.name.endswith("_reg_pkg.sv"):
        return _extract_pkg_facts(path, lines)
    if path.name.endswith("_reg_top.sv"):
        return _extract_reg_top_facts(path, lines)
    if path.name.endswith("_pkg.sv"):
        return _extract_pkg_facts(path, lines)
    return _extract_generic_facts(path, lines)


def _extract_pkg_facts(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    i = 1
    while i <= len(lines):
        line = lines[i - 1]
        stripped = _strip_comment(line).strip()
        if not stripped:
            i += 1
            continue

        param_match = re.match(
            r"^\s*parameter\s+(?:logic\s+\[[^\]]+\]\s+|int(?:\s+unsigned)?\s+|bit\s+)?"
            r"([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?);\s*$",
            stripped,
        )
        if param_match:
            name = param_match.group(1)
            expr = param_match.group(2).strip()
            fact = _fact(
                path,
                i,
                i,
                "param_def",
                name=name,
                expression=expr,
                signals=_name_signals(name),
                tags=["parameterization"],
                rank_score=3,
            )
            facts.append(fact)
            i += 1
            continue

        if re.match(r"^\s*typedef\s+enum\b", stripped):
            end = _find_block_end(lines, i)
            block = "\n".join(lines[i - 1:end])
            enum_name = _extract_typedef_name(block)
            enum_items = _extract_enum_items(block)
            fact = _fact(
                path,
                i,
                end,
                "enum_def",
                name=enum_name,
                expression=block,
                signals=enum_items + _name_signals(enum_name),
                tags=["enum"],
                rank_score=3,
            )
            facts.append(fact)
            i = end + 1
            continue

        if re.match(r"^\s*typedef\s+struct\b", stripped):
            end = _find_block_end(lines, i)
            block = "\n".join(lines[i - 1:end])
            type_name = _extract_typedef_name(block)
            field_names = _extract_struct_fields(block)
            fact = _fact(
                path,
                i,
                end,
                "struct_def",
                name=type_name,
                expression=block,
                signals=field_names + _name_signals(type_name),
                tags=["typedef", "struct"],
                rank_score=2,
            )
            facts.append(fact)
            i = end + 1
            continue

        i += 1
    return facts


def _extract_reg_top_facts(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    i = 1
    while i <= len(lines):
        line = lines[i - 1]
        stripped = _strip_comment(line).strip()
        if not stripped:
            i += 1
            continue

        assign_match = re.match(r"^\s*assign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?);\s*$", stripped)
        if assign_match:
            lhs = assign_match.group(1)
            rhs = assign_match.group(2).strip()
            kind = "assign_guard" if _looks_like_guard(lhs) else "assign_link"
            tags = _assign_tags(lhs, rhs)
            fact = _fact(
                path,
                i,
                i,
                kind,
                name=lhs,
                expression=rhs,
                signals=_expr_signals(lhs + " " + rhs),
                guard_signal=_guard_signal(rhs),
                guard_polarity=_guard_polarity(rhs),
                family=_signal_family(lhs),
                tags=tags,
                rank_score=_rank_for_assign(lhs, rhs),
            )
            facts.append(fact)
            i += 1
            continue

        if _starts_instance(stripped):
            end = _find_instance_end(lines, i)
            block = "\n".join(lines[i - 1:end])
            inst_name = _extract_instance_name(block)
            module_name = _extract_instance_module(block)
            port_map = _extract_named_connections(block)
            param_map = _extract_named_params(block)
            fact = _fact(
                path,
                i,
                end,
                "instance_map",
                name=inst_name or module_name,
                module_name=module_name,
                instance_name=inst_name,
                expression=block,
                signals=sorted(
                    set(
                        _expr_signals(" ".join(port_map.values()))
                        + _name_signals(inst_name or "")
                        + _name_signals(module_name or "")
                    )
                ),
                port_map=port_map,
                param_map=param_map,
                tags=["instance"],
                rank_score=_rank_for_instance(port_map, param_map, module_name, inst_name),
            )
            facts.append(fact)
            i = end + 1
            continue

        i += 1
    return facts


def _extract_generic_facts(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        stripped = _strip_comment(line).strip()
        if not stripped:
            continue
        assign_match = re.match(r"^\s*assign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?);\s*$", stripped)
        if assign_match:
            lhs = assign_match.group(1)
            rhs = assign_match.group(2).strip()
            facts.append(
                _fact(
                    path,
                    idx,
                    idx,
                    "assign_link",
                    name=lhs,
                    expression=rhs,
                    signals=_expr_signals(lhs + " " + rhs),
                    tags=["generic"],
                    rank_score=1,
                )
            )
    return facts


def _fact(
    path: Path,
    start: int,
    end: int,
    kind: str,
    *,
    name: str | None = None,
    expression: str = "",
    signals: list[str] | None = None,
    tags: list[str] | None = None,
    rank_score: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    fact_id = f"{path.stem}__{kind}__{start}_{end}__{_safe_id(name or path.stem)}"
    fact: dict[str, Any] = {
        "fact_id": fact_id,
        "kind": kind,
        "name": name or "",
        "source_file": str(path),
        "line_start": start,
        "line_end": end,
        "expression": expression,
        "normalized_expr": _normalise_expr(expression),
        "signals": sorted(dict.fromkeys(signals or [])),
        "tags": sorted(dict.fromkeys(tags or [])),
        "rank_score": rank_score,
        "evidence_refs": [f"{path.name}:{start}-{end}"],
    }
    fact.update(extra)
    return fact


def _starts_instance(line: str) -> bool:
    return bool(
        re.match(r"^(?:prim_|tlul_|hmac_|cfg_|u_[A-Za-z_][A-Za-z0-9_$]*)", line)
        and "#(" in line
    ) or bool(re.match(r"^[A-Za-z_][A-Za-z0-9_$]*\s+#\(", line))


def _find_block_end(lines: list[str], start: int) -> int:
    depth = 0
    seen_begin = False
    for idx in range(start, len(lines) + 1):
        clean = _strip_comment(lines[idx - 1])
        begins = len(re.findall(r"\bbegin\b", clean))
        ends = len(re.findall(r"\bend\b", clean))
        if begins:
            seen_begin = True
        depth += begins
        depth -= ends
        if seen_begin and depth <= 0:
            return idx
        if re.search(r"}\s*;", clean):
            return idx
    return len(lines)


def _find_instance_end(lines: list[str], start: int) -> int:
    depth = 0
    for idx in range(start, len(lines) + 1):
        clean = _strip_comment(lines[idx - 1])
        depth += clean.count("(") + clean.count("[") + clean.count("{")
        depth -= clean.count(")") + clean.count("]") + clean.count("}")
        if idx > start and depth <= 0 and clean.strip().endswith(");"):
            return idx
    return len(lines)


def _extract_typedef_name(block: str) -> str:
    matches = re.findall(r"}\s*([A-Za-z_][A-Za-z0-9_$]*)\s*;", block)
    return matches[-1] if matches else ""


def _extract_enum_items(block: str) -> list[str]:
    body = re.search(r"\{(.*)\}", block, flags=re.DOTALL)
    if not body:
        return []
    items = []
    for token in body.group(1).split(","):
        token = token.strip()
        if not token:
            continue
        name = token.split("=")[0].strip()
        if _looks_signal_token(name):
            items.append(name)
    return items


def _extract_struct_fields(block: str) -> list[str]:
    fields: list[str] = []
    for line in block.splitlines():
        stripped = _strip_comment(line).strip()
        if not stripped or stripped.startswith("typedef") or stripped.startswith("struct"):
            continue
        if stripped.startswith("}"):
            continue
        match = re.search(r"\b([A-Za-z_][A-Za-z0-9_$]*)\s*;", stripped)
        if match:
            name = match.group(1)
            if _looks_signal_token(name):
                fields.append(name)
    return fields


def _extract_instance_name(block: str) -> str:
    header = block.splitlines()[0]
    match = re.match(r"^\s*[A-Za-z_][A-Za-z0-9_$]*\s*(?:#\s*\(|#\()", header)
    if not match:
        return ""
    after = header[match.end():]
    name_match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_$]*)", after)
    return name_match.group(1) if name_match else ""


def _extract_instance_module(block: str) -> str:
    header = block.splitlines()[0].strip()
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*(?:#\s*\(|#\()", header)
    return match.group(1) if match else ""


def _extract_named_connections(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for port, expr in re.findall(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^)]+?)\s*\)", block):
        out[port] = expr.strip()
    return out


def _extract_named_params(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if "#(" not in block:
        return out
    param_body = block.split("#(", 1)[1].split(")", 1)[0]
    for name, expr in re.findall(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*([^)]+?)\s*\)", param_body):
        out[name] = expr.strip()
    return out


def _looks_like_guard(lhs: str) -> bool:
    lower = lhs.lower()
    return lower.endswith(("_we", "_re", "_sel", "_qe")) or lower in {"addrmiss", "reg_error", "wr_err"}


def _assign_tags(lhs: str, rhs: str) -> list[str]:
    tags: list[str] = []
    lower = (lhs + " " + rhs).lower()
    if any(tok in lower for tok in ("!", "~", "default", "case", "if", "?:")):
        tags.append("conditional")
    if lhs.lower().endswith(("_we", "_re", "_sel", "_qe")):
        tags.append("control_gate")
    if lhs.lower().endswith(("_d", "_q")):
        tags.append("state_or_data")
    if "addr_hit" in lower or "addrmiss" in lower:
        tags.append("address_decode")
    if "prim_" in lower or "instance" in lower:
        tags.append("instance")
    return tags


def _rank_for_assign(lhs: str, rhs: str) -> int:
    score = 1
    lower = (lhs + " " + rhs).lower()
    if lhs.lower().endswith(("_we", "_re", "_sel", "_qe")):
        score += 3
    if any(tok in lower for tok in ("!", "~", "case", "default", "if")):
        score += 2
    if "addr_hit" in lower or "addrmiss" in lower:
        score += 1
    return score


def _rank_for_instance(
    port_map: dict[str, str],
    param_map: dict[str, str],
    module_name: str,
    inst_name: str,
) -> int:
    score = 1
    score += min(3, len(port_map) // 8)
    score += min(2, len(param_map))
    if any(k in module_name.lower() for k in ("prim_", "reg", "tlul")):
        score += 1
    if any(k in inst_name.lower() for k in ("reg", "cfg", "intr", "wipe", "key", "digest")):
        score += 1
    return score


def _guard_signal(expr: str) -> str:
    if "reg_error" in expr:
        return "reg_error"
    if "addrmiss" in expr:
        return "addrmiss"
    if "reg_we" in expr:
        return "reg_we"
    return ""


def _guard_polarity(expr: str) -> str:
    if "reg_error" not in expr:
        return ""
    if any(tok in expr for tok in ("!reg_error", "~reg_error", "not reg_error")):
        return "negative"
    return "positive"


def _signal_family(name: str) -> str:
    if "_" not in name:
        return name
    root = name
    for suffix in _SIGNAL_LIKE_SUFFIXES:
        if root.endswith(suffix) and len(root) > len(suffix):
            root = root[: -len(suffix)]
            break
    return root


def _name_signals(name: str) -> list[str]:
    if not name:
        return []
    out = []
    cleaned = name.replace("__", "_")
    tokens = [tok for tok in cleaned.split("_") if tok]
    if cleaned:
        out.append(cleaned)
    if len(tokens) >= 2:
        out.append("_".join(tokens[-2:]))
    if len(tokens) >= 1:
        out.extend(tokens)
    return sorted(dict.fromkeys(sig for sig in out if _looks_signal_token(sig)))


def _expr_signals(text: str) -> list[str]:
    tokens = []
    for tok in _IDENT_RE.findall(text):
        if _looks_signal_token(tok):
            tokens.append(tok)
    return sorted(dict.fromkeys(tokens))


def _looks_signal_token(name: str) -> bool:
    if not name:
        return False
    lower = name.lower()
    if lower in {"module", "package", "typedef", "struct", "enum", "logic", "int", "parameter"}:
        return False
    if lower.startswith(("hmac_", "cfg_", "key_", "digest_", "msg_", "intr_", "wipe_", "addr_", "reg_", "tl_")):
        return True
    if any(sfx in lower for sfx in ("_we", "_re", "_sel", "_qe", "_q", "_d", "_weq", "_err", "_valid", "_ready", "_en")):
        return True
    return bool("_" in name)


def _normalise_expr(expr: str) -> str:
    return re.sub(r"\s+", " ", expr).strip()


def _strip_comment(line: str) -> str:
    return re.sub(r"//.*$", "", line)


def _safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "item"
