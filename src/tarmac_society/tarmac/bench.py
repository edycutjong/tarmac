"""The ablation sweep: single planner vs society vs society-minus-mediator.

The centerpiece the track text asks for — *"a measurable efficiency gain over
single-agent baselines."* Runs the same seeded storm under three conditions
across N seeds (offline, deterministic), then reports medians + IQR so the
comparison is reproducible rather than a one-shot anecdote.

All three conditions share the identical scenario per seed and the same
ledger physics; only the decision layer differs, so any gap is attributable
to the society, not to luck.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .baseline import run_single_planner
from .metrics import compute_metrics
from .run import run_society
from .seed import generate

__all__ = [
    "CONDITION_ORDER",
    "CONDITION_LABELS",
    "METRIC_ROWS",
    "DEFAULT_SEEDS",
    "AblationResult",
    "run_condition",
    "run_ablation",
    "render_markdown",
]

CONDITION_ORDER = ("single", "society_minus_mediator", "society")
CONDITION_LABELS = {
    "single": "Single planner",
    "society_minus_mediator": "Society − mediator",
    "society": "Full society",
}
# (metric key, display label, lower_is_better)
METRIC_ROWS: tuple[tuple[str, str, bool], ...] = (
    ("protected_stranded", "Protected pax stranded (SLA-failed)", True),
    ("stranded_overnight", "Stranded overnight (no seat)", True),
    ("tight_connections_saved", "Tight connections saved", False),
    ("special_needs_sla_pct", "Special-needs SLA met (%)", False),
    ("crew_violations", "Crew duty violations", True),
    ("rounds_to_quiescence", "Rounds to quiescence", True),
    ("contest_spend", "Contest stake (credibility)", True),
)
DEFAULT_SEEDS = (7, 1, 2, 3, 4, 5, 6, 8, 9, 10)


@dataclass
class AblationResult:
    seeds: list[int]
    # condition -> metric -> list of per-seed values
    raw: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    # condition -> list of full per-seed metric dicts
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def summary(self) -> dict[str, dict[str, dict[str, float]]]:
        """condition -> metric -> {median, q1, q3}."""
        out: dict[str, dict[str, dict[str, float]]] = {}
        for cond, metrics in self.raw.items():
            out[cond] = {}
            for key, values in metrics.items():
                out[cond][key] = _stats(values)
        return out


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0.0, "q1": 0.0, "q3": 0.0}
    med = float(statistics.median(values))
    if len(values) >= 2:
        # inclusive quantiles give q1/q3 that fall on the data for small n
        q = statistics.quantiles(values, n=4, method="inclusive")
        q1, q3 = float(q[0]), float(q[2])
    else:
        q1 = q3 = float(values[0])
    return {"median": med, "q1": q1, "q3": q3}


def run_condition(scenario: dict[str, Any], seed: int, condition: str) -> dict[str, Any]:
    """Run one condition on one seeded scenario; return its metrics row."""
    if condition == "single":
        bundle = run_single_planner(scenario, seed)
        contest_spend = 0
    else:
        bundle = run_society(scenario, seed, condition=condition)
        # credibility PUT AT RISK on contests — the economic cost of arguing
        contest_spend = bundle.bank.total_staked()
    res = bundle.result
    quiescent_round = res.quiescent_round if res.quiescent_round is not None else res.rounds_used
    return compute_metrics(
        bundle.ledger,
        scenario,
        rounds_to_quiescence=quiescent_round,
        contest_spend=contest_spend,
        quiescent=res.quiescent,
    )


def run_ablation(
    seeds: tuple[int, ...] | list[int] = DEFAULT_SEEDS,
    conditions: tuple[str, ...] = CONDITION_ORDER,
) -> AblationResult:
    """Full sweep: every condition × every seed (offline, deterministic)."""
    result = AblationResult(seeds=list(seeds))
    for cond in conditions:
        result.raw[cond] = {key: [] for key, _label, _lb in METRIC_ROWS}
        result.rows[cond] = []
    for seed in seeds:
        scenario = generate("storm_dfw", seed)
        for cond in conditions:
            metrics = run_condition(scenario, seed, cond)
            result.rows[cond].append(metrics)
            for key, _label, _lb in METRIC_ROWS:
                result.raw[cond][key].append(float(metrics[key]))
    return result


def _fmt(stats: dict[str, float], integral: bool) -> str:
    if integral:
        med = f"{stats['median']:.0f}"
        iqr = f"{stats['q1']:.0f}–{stats['q3']:.0f}"
    else:
        med = f"{stats['median']:.1f}"
        iqr = f"{stats['q1']:.1f}–{stats['q3']:.1f}"
    return f"{med} [{iqr}]" if stats["q1"] != stats["q3"] else med


def render_markdown(result: AblationResult, conditions: tuple[str, ...] = CONDITION_ORDER) -> str:
    """Markdown table: metric rows × condition columns, ``median [q1–q3]``."""
    summary = result.summary()
    header = "| Metric | " + " | ".join(CONDITION_LABELS[c] for c in conditions) + " |"
    sep = "|" + "---|" * (len(conditions) + 1)
    lines = [header, sep]
    for key, label, lower_better in METRIC_ROWS:
        integral = key not in ("special_needs_sla_pct",)
        arrow = "↓" if lower_better else "↑"
        cells = [_fmt(summary[c][key], integral) for c in conditions]
        lines.append(f"| {label} ({arrow} better) | " + " | ".join(cells) + " |")
    n = len(result.seeds)
    lines.append("")
    lines.append(
        f"_Medians across {n} seeds; `[q1–q3]` inter-quartile range. "
        f"Offline deterministic policy agents; identical scenario per seed across conditions._"
    )
    return "\n".join(lines)
