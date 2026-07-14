"""The ablation sweep + medians/IQR rendering."""

from __future__ import annotations

from tarmac_society.tarmac import bench as B


def test_stats_median_and_iqr():
    s = B._stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["median"] == 3.0
    assert s["q1"] <= s["median"] <= s["q3"]


def test_stats_single_value():
    s = B._stats([7.0])
    assert s == {"median": 7.0, "q1": 7.0, "q3": 7.0}


def test_stats_empty_values():
    assert B._stats([]) == {"median": 0.0, "q1": 0.0, "q3": 0.0}


def test_run_condition_returns_metrics(scenario):
    m = B.run_condition(scenario, 7, "society")
    assert m["special_needs_sla_pct"] == 100.0
    assert m["crew_violations"] == 0


def test_run_ablation_small_sweep():
    res = B.run_ablation((7, 1))
    assert set(res.raw) == {"single", "society", "society_minus_mediator"}
    assert len(res.rows["society"]) == 2
    summary = res.summary()
    # the headline: full society strands far fewer protected pax than the baseline
    assert summary["society"]["protected_stranded"]["median"] < \
        summary["single"]["protected_stranded"]["median"]
    assert summary["society"]["crew_violations"]["median"] == 0
    assert summary["single"]["crew_violations"]["median"] == 1


def test_render_markdown_has_all_columns():
    res = B.run_ablation((7,))
    table = B.render_markdown(res)
    for label in B.CONDITION_LABELS.values():
        assert label in table
    assert "Protected pax stranded" in table
    assert "Special-needs SLA" in table
    assert table.startswith("| Metric |")


def test_condition_order_and_default_seeds():
    assert B.CONDITION_ORDER == ("single", "society_minus_mediator", "society")
    assert len(B.DEFAULT_SEEDS) == 10 and 7 in B.DEFAULT_SEEDS


def test_full_society_dominates_minus_mediator():
    res = B.run_ablation((7, 3))
    s = res.summary()
    # the mediator is load-bearing: removing it strands more protected pax
    assert s["society"]["protected_stranded"]["median"] < \
        s["society_minus_mediator"]["protected_stranded"]["median"]
