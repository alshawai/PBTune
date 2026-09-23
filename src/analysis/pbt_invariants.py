"""
PBT Algorithmic Invariants
==========================

Properties that must hold for any correct Population-Based Training run,
expressed as checks over a recorded session trace.

Motivation
----------

Diagnosing a misbehaving exploit/explore cycle previously meant reading a
3,500-line HTML log by hand. This module turns that reading into assertions:
each invariant is a pure function from a session trace to a :class:`Finding`
carrying a boolean verdict, a human summary, and the structured numbers the
verdict was derived from.

The checks are deliberately strategy-level, not implementation-level. They
consume only the persisted session schema, so the same code audits a trace
from any PBT run regardless of how the tuner was built or configured.

Invariant catalogue
-------------------

===========================  ==========  =======================================
Invariant                    Bug IDs     Property
===========================  ==========  =======================================
``exploit_cadence``          B1          Exploitation events for the population
                                         are separated by at least the
                                         configured ``ready_interval``.
``donor_diversity``          B2          Elite donors vary across the run rather
                                         than collapsing onto a single worker.
``exploit_recovery``         B4 B5 B6    A worker that exploits an elite recovers
                                         a meaningful fraction of that elite's
                                         throughput in the next generation.
``perturbation_locality``    B4 B5 B6    An explore step perturbs a minority of
                                         the search space, producing a local
                                         neighbour rather than a fresh draw.
``score_metric_coupling``    B7 B8 B9    Population score does not move sharply
                                         while the underlying metrics stand
                                         still.
``normalizer_support``       B7 B8 B9    Normalizer anchors cover the observed
                                         support, so few observations clamp.
``readback_fidelity``        B12         Configuration read-back does not
                                         rewrite a worker's intended knob values
                                         between generations.
``search_efficiency``        B2          The population evaluates meaningfully
                                         more distinct configurations than it has
                                         generations.
``score_rank_agreement``     --          Score ranking broadly agrees with
                                         throughput ranking (sanity check).
===========================  ==========  =======================================

Usage
-----

.. code-block:: python

    from src.analysis.pbt_invariants import SessionTrace, check_all

    trace = SessionTrace.from_path("results/.../trace_20260916_0007.json")
    for finding in check_all(trace):
        print(finding.render())

.. code-block:: bash

    # Audit a trace; exit non-zero if any invariant is violated.
    python -m src.analysis.pbt_invariants \\
        results/sessions/oltp_read_write/pbt/extensive/traces/trace_*.json \\
        --strict
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Finding",
    "Exploitation",
    "SessionTrace",
    "check_all",
    "check_donor_diversity",
    "check_exploit_cadence",
    "check_exploit_recovery",
    "check_normalizer_support",
    "check_perturbation_locality",
    "check_readback_fidelity",
    "check_score_metric_coupling",
    "check_score_rank_agreement",
    "check_search_efficiency",
    "main",
    "violations",
]


#: PostgreSQL sentinel values meaning "derive this from other settings".
#: A read-back resolves them to a concrete number; persisting that number back
#: into the search state destroys the sentinel irreversibly.
AUTOSIZE_SENTINELS: Tuple[int, ...] = (-1, 0)


@dataclass(frozen=True)
class Finding:
    """Verdict for one invariant.

    Attributes
    ----------
    invariant:
        Stable identifier, e.g. ``"exploit_cadence"``.
    bug_ids:
        Ledger IDs this invariant detects. Empty for pure sanity checks.
    holds:
        ``True`` when the property was satisfied.
    summary:
        One-line human-readable verdict.
    observed:
        Structured evidence behind the verdict, suitable for assertions.
    """

    invariant: str
    bug_ids: Tuple[str, ...]
    holds: bool
    summary: str
    observed: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """Format the finding as a single log-friendly line."""
        status = "HOLDS" if self.holds else "VIOLATED"
        tags = f" [{' '.join(self.bug_ids)}]" if self.bug_ids else ""
        return f"[{status:>8}] {self.invariant}{tags}: {self.summary}"


@dataclass(frozen=True)
class Exploitation:
    """One recorded exploit event: ``poor`` cloned ``elite`` at ``generation``."""

    generation: int
    elite_worker_id: int
    poor_worker_id: int


class SessionTrace:
    """Read-only typed view over a persisted PBT session trace.

    Parameters
    ----------
    payload:
        The decoded session JSON.

    Notes
    -----
    Accessors tolerate absent optional blocks (``actual_config`` in particular,
    which pre-dates configuration read-back) by returning ``None``. Invariants
    that need such a block skip the observations that lack it rather than
    failing, so the same checks run against older traces.
    """

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._raw = payload
        self._history: List[Dict[str, Any]] = list(payload.get("history") or [])

    # ------------------------------------------------------------------ load

    @classmethod
    def from_path(cls, path: Path | str) -> "SessionTrace":
        """Load a trace from a JSON file on disk."""
        return cls(json.loads(Path(path).read_text()))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SessionTrace":
        """Wrap an already-decoded trace payload."""
        return cls(payload)

    # ------------------------------------------------------------ session

    @property
    def raw(self) -> Dict[str, Any]:
        """The underlying decoded payload."""
        return self._raw

    @property
    def session(self) -> Dict[str, Any]:
        """The ``tuning_session`` metadata block."""
        return dict(self._raw.get("tuning_session") or {})

    @property
    def strategy_params(self) -> Dict[str, Any]:
        """Strategy-specific parameters recorded for the run."""
        return dict(self.session.get("strategy_params") or {})

    @property
    def ready_interval(self) -> Optional[int]:
        """Configured generations a worker must wait between exploit events."""
        value = self.strategy_params.get("ready_interval")
        return int(value) if value is not None else None

    @property
    def population_size(self) -> int:
        """Number of workers evaluated per generation."""
        if self._history:
            return len(self._history[0].get("worker_scores") or [])
        return int(self.session.get("num_parallel_workers") or 0)

    @property
    def generations(self) -> List[int]:
        """Generation indices present in the trace, in recorded order."""
        return [int(h["generation"]) for h in self._history]

    @property
    def history(self) -> List[Dict[str, Any]]:
        """Raw per-generation records."""
        return self._history

    # ------------------------------------------------------------- lookups

    def _generation(self, generation: int) -> Optional[Dict[str, Any]]:
        for record in self._history:
            if int(record["generation"]) == generation:
                return record
        return None

    def worker_scores(self, generation: int) -> Dict[int, Dict[str, Any]]:
        """Per-worker score records for one generation, keyed by worker id."""
        record = self._generation(generation)
        if record is None:
            return {}
        return {int(w["worker_id"]): w for w in record.get("worker_scores") or []}

    def score(self, generation: int, worker_id: int) -> Optional[float]:
        """Composite score for one worker in one generation."""
        entry = self.worker_scores(generation).get(worker_id)
        return None if entry is None else float(entry["score"])

    def metric(
        self, generation: int, worker_id: int, name: str
    ) -> Optional[float]:
        """One raw metric value for one worker in one generation."""
        entry = self.worker_scores(generation).get(worker_id)
        if entry is None:
            return None
        value = (entry.get("metrics") or {}).get(name)
        return None if value is None else float(value)

    def metric_values(self, name: str) -> List[float]:
        """Every recorded value of one metric across the whole run."""
        values: List[float] = []
        for record in self._history:
            for entry in record.get("worker_scores") or []:
                value = (entry.get("metrics") or {}).get(name)
                if value is not None:
                    values.append(float(value))
        return values

    def intended_config(
        self, generation: int, worker_id: int
    ) -> Optional[Dict[str, Any]]:
        """The knob configuration the tuner chose for this worker."""
        record = self._generation(generation)
        if record is None:
            return None
        for entry in record.get("worker_configs") or []:
            if int(entry["worker_id"]) == worker_id:
                return dict(entry.get("config") or {})
        return None

    def actual_config(
        self, generation: int, worker_id: int
    ) -> Optional[Dict[str, Any]]:
        """The configuration read back from PostgreSQL, when recorded."""
        entry = self.worker_scores(generation).get(worker_id)
        if entry is None:
            return None
        actual = entry.get("actual_config")
        return dict(actual) if actual else None

    @property
    def exploitations(self) -> List[Exploitation]:
        """Every recorded exploit event, in generation order."""
        events: List[Exploitation] = []
        for record in self._history:
            for entry in record.get("exploitations") or []:
                events.append(
                    Exploitation(
                        generation=int(record["generation"]),
                        elite_worker_id=int(entry["elite_worker_id"]),
                        poor_worker_id=int(entry["poor_worker_id"]),
                    )
                )
        return events

    @property
    def normalizer_ranges(self) -> Dict[str, Dict[str, float]]:
        """Final normalizer anchors, keyed by metric id."""
        scoring = self.session.get("scoring") or {}
        metadata = scoring.get("normalization_metadata") or {}
        return dict(metadata.get("ranges") or {})


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


def check_exploit_cadence(trace: SessionTrace) -> Finding:
    """Exploit events must respect the configured ``ready_interval``.

    The PBT specification measures readiness as the steps elapsed *since the
    last time that population member was ready*. A worker that just exploited
    must therefore serve a full cooldown before it may exploit again.
    """
    interval = trace.ready_interval
    active = [
        int(h["generation"])
        for h in trace.history
        if int(h.get("num_exploited") or 0) > 0
    ]
    gaps = [b - a for a, b in zip(active, active[1:], strict=False)]
    observed = {
        "ready_interval": interval,
        "generations_with_exploitation": active,
        "gaps": gaps,
        "min_gap": min(gaps) if gaps else None,
        "short_gaps": sum(1 for g in gaps if interval and g < interval),
    }
    if interval is None or not gaps:
        return Finding(
            "exploit_cadence",
            ("B1",),
            True,
            "no cadence to assess (single or no exploit event)",
            observed,
        )
    short = observed["short_gaps"]
    holds = short == 0
    summary = (
        f"all {len(gaps)} gaps >= ready_interval={interval}"
        if holds
        else (
            f"{short}/{len(gaps)} gaps shorter than ready_interval={interval} "
            f"(min gap {min(gaps)}); the cooldown never re-arms"
        )
    )
    return Finding("exploit_cadence", ("B1",), holds, summary, observed)


def check_donor_diversity(trace: SessionTrace) -> Finding:
    """Truncation selection must sample donors from the elite quantile.

    Sampling uniformly from the top quantile is what keeps the population from
    collapsing onto one lineage. A single donor across an entire run means the
    elite bucket degenerated to a deterministic argmax.
    """
    events = trace.exploitations
    donors = [e.elite_worker_id for e in events]
    distinct = sorted(set(donors))
    observed = {
        "exploitations": len(events),
        "distinct_donors": distinct,
        "donor_sequence": donors,
        "recipients": [e.poor_worker_id for e in events],
    }
    if len(events) < 2:
        return Finding(
            "donor_diversity",
            ("B2",),
            True,
            "fewer than two exploit events; diversity undefined",
            observed,
        )
    holds = len(distinct) > 1
    summary = (
        f"{len(distinct)} distinct donors over {len(events)} exploitations"
        if holds
        else (
            f"worker {distinct[0]} donated all {len(events)} times; the elite "
            f"bucket is a singleton and the population is a monoculture"
        )
    )
    return Finding("donor_diversity", ("B2",), holds, summary, observed)


def check_exploit_recovery(
    trace: SessionTrace, floor: float = 0.85, metric: str = "throughput"
) -> Finding:
    """A worker that copies an elite must land near that elite's performance.

    Exploit copies the donor's configuration wholesale; explore then perturbs
    it. If the perturbation is local, the child performs close to the parent.
    A large shortfall means explore is drawing a fresh configuration rather
    than a neighbour.
    """
    ratios: List[float] = []
    detail: List[Dict[str, Any]] = []
    for event in trace.exploitations:
        donor = trace.metric(event.generation, event.elite_worker_id, metric)
        child = trace.metric(event.generation + 1, event.poor_worker_id, metric)
        if donor is None or child is None or donor == 0:
            continue
        ratio = child / donor
        ratios.append(ratio)
        detail.append(
            {
                "generation": event.generation,
                "elite": event.elite_worker_id,
                "poor": event.poor_worker_id,
                "donor_value": donor,
                "child_value": child,
                "recovery": ratio,
            }
        )
    observed = {
        "metric": metric,
        "floor": floor,
        "n": len(ratios),
        "median_recovery": statistics.median(ratios) if ratios else None,
        "min_recovery": min(ratios) if ratios else None,
        "below_floor": sum(1 for r in ratios if r < floor),
        "transitions": detail,
    }
    if not ratios:
        return Finding(
            "exploit_recovery",
            ("B4", "B5", "B6"),
            True,
            "no exploit transition had comparable measurements",
            observed,
        )
    holds = observed["below_floor"] == 0
    summary = (
        f"all {len(ratios)} children recovered >= {floor:.0%} of donor {metric}"
        if holds
        else (
            f"{observed['below_floor']}/{len(ratios)} children below {floor:.0%} "
            f"of donor {metric} (median {statistics.median(ratios):.1%}, "
            f"min {min(ratios):.1%}); explore is not producing local neighbours"
        )
    )
    return Finding("exploit_recovery", ("B4", "B5", "B6"), holds, summary, observed)


def check_perturbation_locality(
    trace: SessionTrace, max_fraction: float = 0.5
) -> Finding:
    """Explore must move a minority of the search space in one step.

    Perturbing every dimension at once in a 170-knob space makes the child
    statistically independent of the parent, which erases the locality that
    makes exploit/explore a hill climb rather than random search.
    """
    rows: List[Dict[str, Any]] = []
    for event in trace.exploitations:
        parent = trace.intended_config(event.generation, event.elite_worker_id)
        child = trace.intended_config(event.generation + 1, event.poor_worker_id)
        if not parent or not child:
            continue
        shared = set(parent) & set(child)
        if not shared:
            continue
        changed = [k for k in shared if parent[k] != child[k]]
        rows.append(
            {
                "generation": event.generation,
                "elite": event.elite_worker_id,
                "poor": event.poor_worker_id,
                "changed": len(changed),
                "comparable": len(shared),
                "fraction": len(changed) / len(shared),
            }
        )
    fractions = [r["fraction"] for r in rows]
    observed = {
        "max_fraction": max_fraction,
        "n": len(rows),
        "median_fraction": statistics.median(fractions) if fractions else None,
        "max_observed": max(fractions) if fractions else None,
        "over_limit": sum(1 for f in fractions if f > max_fraction),
        "events": rows,
    }
    if not rows:
        return Finding(
            "perturbation_locality",
            ("B4", "B5", "B6"),
            True,
            "no exploit transition had comparable configurations",
            observed,
        )
    holds = observed["over_limit"] == 0
    summary = (
        f"every explore step moved <= {max_fraction:.0%} of knobs"
        if holds
        else (
            f"{observed['over_limit']}/{len(rows)} explore steps moved more than "
            f"{max_fraction:.0%} of knobs (median "
            f"{statistics.median(fractions):.0%}, max {max(fractions):.0%})"
        )
    )
    return Finding(
        "perturbation_locality", ("B4", "B5", "B6"), holds, summary, observed
    )


def check_score_metric_coupling(
    trace: SessionTrace,
    score_tolerance: float = 10.0,
    metric_tolerance: float = 0.05,
    metric: str = "throughput",
) -> Finding:
    """Score must not move sharply while the measured metrics stand still.

    The composite score is a ruler laid over raw metrics. When the ruler itself
    is rescaled mid-run, the reported score jumps without anything about the
    database having changed, and the convergence curve stops measuring tuning
    progress.
    """
    shocks: List[Dict[str, Any]] = []
    series: List[Dict[str, Any]] = []
    previous_metric: Optional[float] = None
    previous_score: Optional[float] = None
    for record in trace.history:
        generation = int(record["generation"])
        entries = record.get("worker_scores") or []
        values = [
            float(w["metrics"][metric])
            for w in entries
            if (w.get("metrics") or {}).get(metric) is not None
        ]
        if not values:
            continue
        mean_metric = statistics.mean(values)
        mean_score = float(record["mean_score"])
        if previous_metric is not None and previous_score is not None:
            metric_delta = (mean_metric - previous_metric) / previous_metric
            score_delta = mean_score - previous_score
            series.append(
                {
                    "generation": generation,
                    "metric_delta": metric_delta,
                    "score_delta": score_delta,
                }
            )
            if (
                abs(score_delta) > score_tolerance
                and abs(metric_delta) < metric_tolerance
            ):
                shocks.append(
                    {
                        "generation": generation,
                        "metric_delta": metric_delta,
                        "score_delta": score_delta,
                        "mean_metric": mean_metric,
                        "mean_score": mean_score,
                    }
                )
        previous_metric, previous_score = mean_metric, mean_score
    observed = {
        "metric": metric,
        "score_tolerance": score_tolerance,
        "metric_tolerance": metric_tolerance,
        "shocks": shocks,
        "series": series,
    }
    holds = not shocks
    summary = (
        "no score move without a matching metric move"
        if holds
        else "; ".join(
            f"gen {s['generation']}: {metric} {s['metric_delta']:+.1%} but "
            f"score {s['score_delta']:+.1f}"
            for s in shocks
        )
    )
    return Finding(
        "score_metric_coupling", ("B7", "B8", "B9"), holds, summary, observed
    )


def check_normalizer_support(
    trace: SessionTrace, max_saturated: float = 0.25
) -> Finding:
    """Normalizer anchors must cover the support the population actually visits.

    Observations outside the anchors clamp to utility 0 or 1, so distinct
    workers score identically and the composite score stops discriminating
    exactly where selection pressure matters most.
    """
    rows: List[Dict[str, Any]] = []
    for name, bounds in trace.normalizer_ranges.items():
        values = trace.metric_values(name)
        if not values:
            continue
        low, high = float(bounds["low"]), float(bounds["high"])
        below = sum(1 for v in values if v <= low)
        above = sum(1 for v in values if v >= high)
        rows.append(
            {
                "metric": name,
                "low": low,
                "high": high,
                "observed_min": min(values),
                "observed_max": max(values),
                "n": len(values),
                "clamped_low": below / len(values),
                "clamped_high": above / len(values),
                "clamped": (below + above) / len(values),
            }
        )
    breaches = [r for r in rows if r["clamped"] > max_saturated]
    observed = {
        "max_saturated": max_saturated,
        "metrics": rows,
        "breaches": [r["metric"] for r in breaches],
    }
    holds = not breaches
    summary = (
        f"every metric clamps <= {max_saturated:.0%} of observations"
        if holds
        else "; ".join(
            f"{r['metric']} clamps {r['clamped']:.0%}" for r in breaches
        )
    )
    return Finding("normalizer_support", ("B7", "B8", "B9"), holds, summary, observed)


def check_readback_fidelity(trace: SessionTrace) -> Finding:
    """Configuration read-back must not rewrite a worker's intended knobs.

    PostgreSQL reports auto-sized parameters resolved to concrete numbers, so a
    read-back of ``wal_buffers = -1`` returns ``512``. Merging that reading back
    into the search state replaces the sentinel with a fixed value that can
    never be re-proposed, silently shrinking the search space one generation at
    a time.

    Detection is restricted to workers that exploited at neither end of the
    transition, so a changed value cannot be attributed to cloning or
    perturbation. Excluding the *destination* generation matters: a worker that
    exploits at ``G+1`` inherits the donor's value, which may coincide with its
    own read-back and would otherwise read as a false ratchet.
    """
    exploited_at = {(e.generation, e.poor_worker_id) for e in trace.exploitations}
    ratchets: List[Dict[str, Any]] = []
    compared = 0
    generations = trace.generations
    for current, following in zip(generations, generations[1:], strict=False):
        for worker_id in trace.worker_scores(current):
            if (current, worker_id) in exploited_at:
                continue
            if (following, worker_id) in exploited_at:
                continue
            intended = trace.intended_config(current, worker_id)
            actual = trace.actual_config(current, worker_id)
            next_intended = trace.intended_config(following, worker_id)
            if not intended or not actual or not next_intended:
                continue
            compared += 1
            for knob in set(intended) & set(actual) & set(next_intended):
                was, readback, now = (
                    intended[knob],
                    actual[knob],
                    next_intended[knob],
                )
                if was != readback and now == readback:
                    ratchets.append(
                        {
                            "generation": current,
                            "worker_id": worker_id,
                            "knob": knob,
                            "intended": was,
                            "readback": readback,
                            "carried_forward": now,
                            "was_sentinel": was in AUTOSIZE_SENTINELS,
                        }
                    )
    sentinel_losses = [r for r in ratchets if r["was_sentinel"]]
    knobs_affected = sorted({str(r["knob"]) for r in ratchets})
    observed = {
        "worker_generations_compared": compared,
        "ratchets": len(ratchets),
        "sentinel_losses": len(sentinel_losses),
        "knobs_affected": knobs_affected,
        "examples": ratchets[:10],
    }
    holds = not ratchets
    summary = (
        "read-back never overwrote an intended knob value"
        if holds
        else (
            f"{len(ratchets)} knob values were replaced by their read-back and "
            f"carried into the next generation ({len(sentinel_losses)} destroyed "
            f"an auto-size sentinel) across "
            f"{len(knobs_affected)} distinct knobs"
        )
    )
    return Finding("readback_fidelity", ("B12",), holds, summary, observed)


def check_search_efficiency(
    trace: SessionTrace, min_ratio: float = 0.25
) -> Finding:
    """The population must keep exploring new configurations.

    Every generation costs one full benchmark per worker. If the population
    holds only a handful of distinct configurations, most of that budget is
    re-measuring the same configuration and buying nothing but noise.
    """
    seen: set[str] = set()
    evaluations = 0
    per_generation: List[Dict[str, Any]] = []
    for generation in trace.generations:
        distinct_now: set[str] = set()
        for worker_id in sorted(trace.worker_scores(generation)):
            config = trace.intended_config(generation, worker_id)
            if config is None:
                continue
            evaluations += 1
            key = json.dumps(config, sort_keys=True, default=str)
            seen.add(key)
            distinct_now.add(key)
        per_generation.append(
            {"generation": generation, "distinct_in_population": len(distinct_now)}
        )
    ratio = len(seen) / evaluations if evaluations else 0.0
    observed = {
        "min_ratio": min_ratio,
        "distinct_configurations": len(seen),
        "evaluations": evaluations,
        "ratio": ratio,
        "per_generation": per_generation,
    }
    holds = ratio >= min_ratio
    summary = (
        f"{len(seen)} distinct configurations over {evaluations} evaluations "
        f"({ratio:.0%})"
        if holds
        else (
            f"only {len(seen)} distinct configurations over {evaluations} "
            f"evaluations ({ratio:.0%} < {min_ratio:.0%}); the population is "
            f"re-measuring frozen configurations"
        )
    )
    return Finding("search_efficiency", ("B2",), holds, summary, observed)


def check_score_rank_agreement(
    trace: SessionTrace,
    max_inversion: float = 0.15,
    metric: str = "throughput",
    tie_band: float = 0.02,
) -> Finding:
    """Score ranking should broadly track the headline metric ranking.

    A sanity check rather than a defect probe: the composite score weights many
    metrics, so it need not agree perfectly, but wholesale disagreement would
    mean selection pressure points away from performance.
    """
    inversions = 0
    compared = 0
    for generation in trace.generations:
        entries = list(trace.worker_scores(generation).values())
        for left, right in combinations(entries, 2):
            lv = (left.get("metrics") or {}).get(metric)
            rv = (right.get("metrics") or {}).get(metric)
            if lv is None or rv is None:
                continue
            lv, rv = float(lv), float(rv)
            if max(lv, rv) == 0 or abs(lv - rv) / max(lv, rv) < tie_band:
                continue
            compared += 1
            if (lv > rv) != (float(left["score"]) > float(right["score"])):
                inversions += 1
    rate = inversions / compared if compared else 0.0
    observed = {
        "max_inversion": max_inversion,
        "metric": metric,
        "pairs_compared": compared,
        "inversions": inversions,
        "inversion_rate": rate,
    }
    holds = compared > 0 and rate <= max_inversion
    summary = (
        f"{inversions}/{compared} pairs rank-inverted ({rate:.1%})"
        if compared
        else "no comparable worker pairs"
    )
    return Finding("score_rank_agreement", (), holds, summary, observed)


#: Every invariant, in reporting order.
CHECKS: Tuple[Callable[[SessionTrace], Finding], ...] = (
    check_exploit_cadence,
    check_donor_diversity,
    check_exploit_recovery,
    check_perturbation_locality,
    check_score_metric_coupling,
    check_normalizer_support,
    check_readback_fidelity,
    check_search_efficiency,
    check_score_rank_agreement,
)


def check_all(trace: SessionTrace) -> List[Finding]:
    """Run every invariant against ``trace`` and return the findings in order."""
    return [check(trace) for check in CHECKS]


def violations(findings: Sequence[Finding]) -> List[Finding]:
    """Filter ``findings`` down to the violated invariants."""
    return [f for f in findings if not f.holds]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.analysis.pbt_invariants",
        description="Audit a PBT session trace against the algorithm's invariants.",
    )
    p.add_argument(
        "traces",
        nargs="+",
        help="Session trace JSON file(s) to audit.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format (default: text).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any invariant is violated.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Audit one or more traces and report the findings.

    Returns ``1`` under ``--strict`` when any invariant was violated, so the
    post-fix validation sweep can gate on a clean run.
    """
    args = _build_arg_parser().parse_args(argv)
    report: Dict[str, Any] = {}
    broken = 0

    for path in args.traces:
        trace = SessionTrace.from_path(path)
        findings = check_all(trace)
        broken += len(violations(findings))
        if args.format == "json":
            report[str(path)] = [
                {
                    "invariant": f.invariant,
                    "bug_ids": list(f.bug_ids),
                    "holds": f.holds,
                    "summary": f.summary,
                    "observed": f.observed,
                }
                for f in findings
            ]
        else:
            print(f"\n{path}")
            print(
                f"  population={trace.population_size} "
                f"generations={len(trace.generations)} "
                f"ready_interval={trace.ready_interval}"
            )
            for finding in findings:
                print(f"  {finding.render()}")

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"\n{broken} invariant violation(s) across {len(args.traces)} trace(s)")

    return 1 if (args.strict and broken) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
