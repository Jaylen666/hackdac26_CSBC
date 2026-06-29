"""
SVA Bind File Rendering (Formal CSBC v2.0 §9, v2.5 fix 2)
===========================================================

Generates a SystemVerilog bind file that wraps an assertion in a checker module
and binds it to the target module. Handles signal width declarations using
widths extracted from RTL (via yosys), defaulting to single-bit when unknown.

The bind wrapper exposes the assertion to SymbiYosys while keeping the original
RTL unchanged.
"""

from __future__ import annotations


def render_sva_bind(
    *,
    sva_text: str,
    bind_module: str,
    bind_signals: list[str],
    clock: str,
    reset: str,
    signal_widths: dict[str, int] | None = None,
) -> str:
    """Render a SystemVerilog bind file for the given SVA.

    Args:
        sva_text: The complete assertion statement (e.g., "assert property ...").
        bind_module: Target module name to bind to.
        bind_signals: Signal names referenced in the assertion (for port list).
        clock: Clock signal name.
        reset: Reset signal name.
        signal_widths: Dict mapping signal names to bit widths. Signals not in
                       this dict default to 1 bit.

    Returns:
        SystemVerilog source text for the bind file.
    """
    signal_widths = signal_widths or {}

    # Build port list with widths.
    ports = []
    for sig in bind_signals:
        width = signal_widths.get(sig, 1)
        if width == 1:
            ports.append(f"    input wire {sig}")
        else:
            ports.append(f"    input wire [{width-1}:0] {sig}")

    # Clock and reset are always added (if not already in bind_signals).
    if clock and clock not in bind_signals:
        ports.append(f"    input wire {clock}")
    if reset and reset not in bind_signals:
        ports.append(f"    input wire {reset}")

    ports_str = ",\n".join(ports)

    # Generate the checker module.
    checker = f"""\
module sva_checker (
{ports_str}
);

{sva_text}

endmodule
"""

    # Generate the bind wrapper that instantiates and binds the checker.
    # The wrapper is what sby will use as `-top`.
    bind_statement = f"bind {bind_module} sva_checker sva_checker_inst (.*);"

    wrapper = f"""\
module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

{checker}

{bind_statement}
"""

    return wrapper
