"""
Design hierarchy retrieval: trace module instantiation tree
and build the complete signal driver→consumer graph.

The key CSBC insight: a bug is only reachable if the driven signal
has at least one consumer chunk that assumes something about it.
Driven-but-unconsumed signals are dead — skip them.
Consumed-but-undriven signals are dangling assumptions — flag them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ModuleInst:
    name: str              # instance name (e.g. u_hmac_core)
    module_type: str       # module type (e.g. hmac_core)
    file: str              # source file
    ports: dict[str, str]  # { port_name: connected_net }
    children: list[ModuleInst] = field(default_factory=list)


@dataclass
class SignalInfo:
    drivers: list[str] = field(default_factory=list)   # chunk_ids that drive this signal
    consumers: list[str] = field(default_factory=list)  # chunk_ids that read this signal
    instance_path: str = ""


def find_module_file(module_name: str, search_dirs: list[Path]) -> Path | None:
    """Find the .sv file containing a module definition."""
    for d in search_dirs:
        for sv in d.rglob("*.sv"):
            content = sv.read_text(encoding="utf-8", errors="ignore")
            if re.search(rf"^\s*module\s+{re.escape(module_name)}\b", content, re.MULTILINE):
                return sv
    return None


def extract_instances(file_path: Path | str) -> list[tuple[str, str, dict[str, str], int]]:
    """Extract all module instances from a file.

    Returns: [(instance_name, module_type, {port: net}, line_number)]
    """
    content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    instances: list[tuple[str, str, dict[str, str], int]] = []

    # Match: module_type #(...)? inst_name ( .port(net), ... );
    # DOTALL makes . match newlines, so multi-line param/port blocks work.
    pattern = re.compile(
        r"^\s*"
        r"(\w+)"                           # module_type
        r"(?:\s*#\s*\([\s\S]*?\)\s*)?"    # optional multi-line #(params)
        r"\s+"
        r"(u_\w+|prim_\w+)"               # instance name (starts with u_ or prim_)
        r"\s*\("
        r"([\s\S]*?)"                     # port connections (non-greedy)
        r"\)\s*;",                         # closing
        re.MULTILINE,
    )

    for m in pattern.finditer(content):
        prefix = m.group(1)
        ports_str = m.group(2)

        mod_type = m.group(1)
        inst_name = m.group(2)
        ports_str = m.group(3)

        skip_words = {
            "assign", "always", "always_comb", "always_ff", "always_latch",
            "module", "endmodule", "if", "else", "for", "foreach", "while",
            "generate", "endgenerate", "begin", "end",
            "case", "endcase", "casex", "casez",
            "property", "assert", "assume", "cover",
            "int", "logic", "wire", "reg", "bit",
            "unique", "priority", "input", "output", "inout",
        }
        if mod_type in skip_words:
            continue
        line_no = content[: m.start()].count("\n") + 1

        # Skip if it looks like a keyword or control flow
        if mod_type in (
            "assign", "always", "always_comb", "always_ff", "always_latch",
            "module", "endmodule", "if", "else", "for", "foreach", "while",
            "generate", "endgenerate", "begin", "end",
            "case", "endcase", "casex", "casez",
            "property", "assert", "assume", "cover",
            "int", "logic", "wire", "reg", "bit",
            "unique", "priority", "input", "output", "inout",
        ):
            continue

        # Parse port connections: .port_name ( net_name ),
        ports: dict[str, str] = {}
        for pm in re.finditer(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_.\[\]]*)\s*\)", ports_str):
            port, net = pm.group(1), pm.group(2)
            ports[port] = net

        instances.append((inst_name, mod_type, ports, line_no))

    return instances


def build_hierarchy(
    top_module: str,
    top_file: Path | str,
    search_dirs: list[Path],
    max_depth: int = 20,
) -> ModuleInst:
    """Recursively build the instance hierarchy starting from top_file."""
    visited: set[str] = set()

    def _build(file: Path | str, depth: int = 0) -> ModuleInst | None:
        if depth > max_depth:
            return None

        file = Path(file)
        instances = extract_instances(file)
        if not instances:
            return None

        # For simplicity, return the first module found in the file
        content = file.read_text(encoding="utf-8", errors="ignore")
        mod_m = re.search(r"^\s*module\s+(\w+)", content, re.MULTILINE)
        if not mod_m:
            return None

        module_name = mod_m.group(1)
        root = ModuleInst(name="<top>", module_type=module_name, file=str(file), ports={})

        for inst_name, mod_type, ports, line_no in instances:
            child = ModuleInst(name=inst_name, module_type=mod_type, file=f"{file}:{line_no}", ports=ports)
            # Recursively find submodule
            if mod_type not in visited:
                visited.add(mod_type)
                sub_file = find_module_file(mod_type, search_dirs)
                if sub_file:
                    sub = _build(sub_file, depth + 1)
                    if sub:
                        child.children = sub.children
            root.children.append(child)

        return root

    return _build(top_file) or ModuleInst(name="<top>", module_type=top_module, file=str(top_file), ports={})


def flatten_instances(root: ModuleInst, prefix: str = "") -> list[tuple[str, str, str, dict[str, str]]]:
    """Flatten the hierarchy into a list of (instance_path, module_type, file, ports)."""
    result: list[tuple[str, str, str, dict[str, str]]] = []
    for child in root.children:
        path = f"{prefix}.{child.name}" if prefix else child.name
        result.append((path, child.module_type, child.file, child.ports))
        result.extend(flatten_instances(child, path))
    return result


def build_signal_flow(
    instances: list[tuple[str, str, str, dict[str, str]]],
) -> dict[str, SignalInfo]:
    """Build driver→consumer graph from instance port connections.

    A signal is driven by an instance if it appears as an output port connection.
    A signal is consumed if it appears as an input port connection.

    Since we don't have port direction info, we track all connections.
    """
    flow: dict[str, SignalInfo] = defaultdict(SignalInfo)

    for inst_path, mod_type, file, ports in instances:
        for port, net in ports.items():
            if net not in flow:
                flow[net] = SignalInfo()
            if not flow[net].instance_path:
                flow[net].instance_path = inst_path
            # Add both driver and consumer (we can't distinguish direction without port decls)
            flow[net].drivers.append(f"{mod_type}.{port}@{inst_path}")

    return flow


def hierarchy_coverage(
    flow: dict[str, SignalInfo],
    chunk_signals: set[str],
) -> dict[str, Any]:
    """Report CSBC coverage: which signals are driven, consumed, both, or neither.

    Returns:
      - covered: signals with both drivers and consumers (CSBC-reachable)
      - dangling_drivers: signals driven but never consumed (dead — CSBC can't see them)
      - dangling_consumers: signals consumed but never driven (dangling assumption)
      - unreachable: signals with neither drivers nor consumers (likely internal wires)
    """
    covered = {}
    dangling_drivers = {}
    dangling_consumers = {}
    unreachable = {}

    for sig, info in flow.items():
        if info.drivers and info.consumers:
            covered[sig] = info
        elif info.drivers and not info.consumers:
            dangling_drivers[sig] = info
        elif not info.drivers and info.consumers:
            dangling_consumers[sig] = info
        else:
            unreachable[sig] = info

    # Filter to signals that appear in at least one chunk
    chunk_reachable = {s: info for s, info in covered.items() if s in chunk_signals}
    chunk_dangling = {s: info for s, info in dangling_drivers.items() if s in chunk_signals}

    return {
        "covered": chunk_reachable,
        "dangling_drivers": chunk_dangling,
        "dangling_consumers": dangling_consumers,
        "total_signals": len(flow),
        "reachable_bugs": len(chunk_reachable),
    }


def print_hierarchy(root: ModuleInst, indent: int = 0) -> None:
    """Pretty-print the instance hierarchy."""
    prefix = "  " * indent
    print(f"{prefix}{root.module_type} [{root.file}]")
    for child in root.children:
        print(f"{prefix}  {child.name}: {child.module_type}")
        for sub in child.children:
            print(f"{prefix}    {sub.name}: {sub.module_type}")


if __name__ == "__main__":
    import sys
    top_file = sys.argv[1] if len(sys.argv) > 1 else "/home/AM/hack2dac/opentitan/hw/ip/hmac/rtl/hmac.sv"
    search = [Path("/home/AM/hack2dac/opentitan/hw/ip/hmac/rtl"),
              Path("/home/AM/hack2dac/opentitan/hw/ip/prim/rtl"),
              Path("/home/AM/hack2dac/opentitan/hw/ip/tlul/rtl")]

    root = build_hierarchy("hmac", top_file, search)
    print_hierarchy(root)
