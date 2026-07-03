"""
CSBC coverage and readiness metrics.

For each chunk and for the total design, compute:
  - Signal coverage: guarantees_written / signals_driven
  - Assumption coverage: assumptions_written / signals_read
  - CSBC reachability: signals with both driver and consumer
  - Uncertainty rate: high_uncertainty_clauses / total
  - Formal consistency: matching NL ↔ formal pairs / total dual spec pairs

Overall readiness: reachability × (1 - uncertainty_rate) × consistency
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from csbc3.chunker import Chunk


@dataclass
class ChunkMetrics:
    chunk_id: str
    construct_type: str
    signals_driven: int = 0
    signals_read: int = 0
    guarantees_written: int = 0
    assumptions_written: int = 0
    formalizable_count: int = 0
    nl_uncertain_high: int = 0
    signal_coverage: float = 0.0
    assumption_coverage: float = 0.0
    nl_formal_mismatch: int = 0
    formal_consistency: float = 1.0


@dataclass
class DesignMetrics:
    total_chunks: int = 0
    total_signals_in_graph: int = 0
    total_signals_driven: int = 0
    total_signals_read: int = 0
    total_guarantees: int = 0
    total_assumptions: int = 0
    total_formalizable: int = 0
    total_high_uncertainty: int = 0
    total_nl_formal_mismatches: int = 0
    total_dual_spec_pairs: int = 0

    # Signal graph metrics
    signals_with_driver: int = 0
    signals_with_consumer: int = 0
    signals_with_both: int = 0
    signals_dangling_driver: int = 0  # driven but no consumer
    signals_dangling_assumption: int = 0  # consumed but no driver

    # Aggregate scores
    signal_coverage: float = 0.0
    assumption_coverage: float = 0.0
    csbc_reachability: float = 0.0
    uncertainty_rate: float = 0.0
    formal_consistency: float = 1.0
    readiness_score: float = 0.0

    per_chunk: list[ChunkMetrics] = field(default_factory=list)

    def compute(self):
        if self.total_signals_driven:
            self.signal_coverage = self.total_guarantees / self.total_signals_driven
        if self.total_signals_read:
            self.assumption_coverage = self.total_assumptions / self.total_signals_read
        if self.total_signals_in_graph:
            self.csbc_reachability = self.signals_with_both / self.total_signals_in_graph
        if self.total_dual_spec_pairs:
            self.formal_consistency = 1.0 - (self.total_nl_formal_mismatches / self.total_dual_spec_pairs) if self.total_dual_spec_pairs > 0 else 1.0
        total = self.total_formalizable + self.total_high_uncertainty
        if total:
            self.uncertainty_rate = self.total_high_uncertainty / total
        self.readiness_score = (
            self.csbc_reachability
            * (1.0 - self.uncertainty_rate)
            * self.formal_consistency
        )

    def summary(self) -> str:
        self.compute()
        return (
            f"CSBC Readiness: {self.readiness_score:.2%}\n"
            f"  Signal coverage:      {self.signal_coverage:.1%} ({self.total_guarantees}/{self.total_signals_driven})\n"
            f"  Assumption coverage:  {self.assumption_coverage:.1%} ({self.total_assumptions}/{self.total_signals_read})\n"
            f"  CSBC reachability:    {self.csbc_reachability:.1%} ({self.signals_with_both}/{self.total_signals_in_graph})\n"
            f"  Uncertainty rate:     {self.uncertainty_rate:.1%} ({self.total_high_uncertainty}/{total})\n"
            f"  Formal consistency:   {self.formal_consistency:.1%}\n"
            f"\n"
            f"  Dangling drivers (driven but no consumer):  {self.signals_dangling_driver}\n"
            f"  Dangling assumptions (consumed but no driver): {self.signals_dangling_assumption}"
        )


def compute_metrics(
    chunks: list[Chunk],
    assign_clauses: list[dict],
    always_results: list,
    signal_graph: dict[str, dict],
) -> DesignMetrics:
    """Compute all metrics from the pipeline outputs."""
    metrics = DesignMetrics()
    metrics.total_chunks = len(chunks)

    # Assign clauses
    assign_guarantees = set()
    for c in assign_clauses:
        sig = c.get("signal", "")
        if sig:
            assign_guarantees.add(sig)
            metrics.total_guarantees += 1
            metrics.total_formalizable += 1  # assign is always formalizable

    # Always results
    always_signals = set()
    for r in always_results:
        always_signals.add(r.signal)
        metrics.total_guarantees += 1
        if r.formalizable:
            metrics.total_formalizable += 1
        if r.nl_uncertainty == "high":
            metrics.total_high_uncertainty += 1
        if r.cross_check == "mismatch":
            metrics.total_nl_formal_mismatches += 1
            metrics.total_dual_spec_pairs += 1
        elif r.formalizable and r.nl_claim:
            metrics.total_dual_spec_pairs += 1

    # Chunk-level metrics
    for chunk in chunks:
        cm = ChunkMetrics(
            chunk_id=chunk.chunk_id,
            construct_type=chunk.construct_type,
            signals_driven=len(chunk.driven_signals),
            signals_read=len(chunk.read_signals),
        )
        # Count how many of its driven signals have guarantees
        for sig in chunk.driven_signals:
            if sig in assign_guarantees or sig in always_signals:
                cm.guarantees_written += 1
        # Count how many of its read signals have assumptions (tricky - assumptions
        # are on the consumer side, not the driver side. Approximate: count driven
        # signals that appear as read in other chunks)
        cm.signal_coverage = (
            cm.guarantees_written / cm.signals_driven if cm.signals_driven > 0 else 1.0
        )
        metrics.per_chunk.append(cm)

    # Signal graph metrics
    all_signals = set(signal_graph.keys())
    for sig, info in signal_graph.items():
        drivers = info.get("drivers", [])
        consumers = info.get("consumers", [])
        if drivers:
            metrics.signals_with_driver += 1
        if consumers:
            metrics.signals_with_consumer += 1
        if drivers and consumers:
            metrics.signals_with_both += 1
        if drivers and not consumers:
            metrics.signals_dangling_driver += 1
        if consumers and not drivers:
            metrics.signals_dangling_assumption += 1

    metrics.total_signals_in_graph = len(all_signals)

    # Total driven/read from chunks
    driven_set = set()
    read_set = set()
    for c in chunks:
        driven_set.update(c.driven_signals)
        read_set.update(c.read_signals)
    metrics.total_signals_driven = len(driven_set)
    metrics.total_signals_read = len(read_set)

    metrics.compute()
    return metrics
