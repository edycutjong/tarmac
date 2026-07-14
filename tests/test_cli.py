"""The ``tarmac`` CLI end-to-end (offline, zero-key)."""

from __future__ import annotations

from typer.testing import CliRunner

from tarmac_society.cli import app

runner = CliRunner()


def test_seed_writes_fixture(tmp_path):
    out = tmp_path / "fix.json"
    r = runner.invoke(app, ["seed", "--seed", "7", "--out", str(out)])
    assert r.exit_code == 0
    assert out.exists()
    from tarmac_society.tarmac.seed import load_fixture

    assert len(load_fixture(out)["pax"]) == 180


def test_run_society_prints_metrics():
    r = runner.invoke(app, ["run", "--condition", "society", "--seed", "7"])
    assert r.exit_code == 0
    assert "special-needs SLA" in r.stdout
    assert "100.0%" in r.stdout


def test_run_single_condition():
    r = runner.invoke(app, ["run", "--condition", "single", "--seed", "7"])
    assert r.exit_code == 0
    assert "crew violations" in r.stdout


def test_run_writes_db_then_verify_log_ok(tmp_path):
    db = tmp_path / "run.db"
    r = runner.invoke(app, ["run", "--condition", "society", "--seed", "7", "--db", str(db)])
    assert r.exit_code == 0 and db.exists()
    v = runner.invoke(app, ["verify-log", str(db)])
    assert v.exit_code == 0
    assert "overall: OK" in v.stdout


def test_verify_log_flags_single_planner(tmp_path):
    db = tmp_path / "single.db"
    runner.invoke(app, ["run", "--condition", "single", "--seed", "7", "--db", str(db)])
    v = runner.invoke(app, ["verify-log", str(db)])
    assert v.exit_code == 1  # I2 crew-duty fails for the baseline
    assert "I2.crew_duty" in v.stdout


def test_replay_matches(tmp_path):
    db = tmp_path / "run.db"
    runner.invoke(app, ["run", "--condition", "society", "--seed", "7", "--db", str(db)])
    r = runner.invoke(app, ["replay", str(db)])
    assert r.exit_code == 0
    assert "matches recorded manifest: True" in r.stdout


def test_run_from_fixture(tmp_path):
    fix = tmp_path / "fix.json"
    runner.invoke(app, ["seed", "--seed", "7", "--out", str(fix)])
    r = runner.invoke(app, ["run", "--fixture", str(fix), "--condition", "society"])
    assert r.exit_code == 0


def test_run_single_with_live_flag_notes_no_effect():
    # --live has no effect on the greedy single planner: prints a note and
    # still runs fully offline (no DASHSCOPE_API_KEY required).
    r = runner.invoke(app, ["run", "--condition", "single", "--seed", "7", "--live"])
    assert r.exit_code == 0
    assert "note: --live has no effect on the greedy single planner" in r.output


def test_run_live_without_key_fails_at_client_guard(monkeypatch):
    # --live on a real (non-single) condition constructs a LiveQwen transport;
    # without DASHSCOPE_API_KEY the very first agent decision hits the guard
    # clause in LiveQwen.client and raises — offline runs never take this path.
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    r = runner.invoke(app, ["run", "--condition", "society", "--seed", "7", "--live"])
    assert r.exit_code != 0
    assert r.exception is not None


def test_bench_writes_markdown_file(tmp_path):
    out = tmp_path / "bench.md"
    r = runner.invoke(app, ["bench", "--seeds", "7,1", "--out", str(out)])
    assert r.exit_code == 0
    assert out.exists()
    assert "Full society" in out.read_text()
    assert f"written to {out}" in r.output


def test_verify_log_skips_domain_check_for_non_storm_dfw_genesis(tmp_path):
    # A fixture whose scenario "name" isn't a known SCENARIOS entry makes
    # generate() raise inside verify-log's domain-check setup; that failure
    # must be swallowed (only the airline-specific I2 check is skipped).
    from tarmac_society.tarmac.seed import generate, write_fixture

    sc = generate("storm_dfw", 7)
    sc["name"] = "not_a_real_scenario"
    fix = tmp_path / "custom.json"
    write_fixture(sc, fix)

    db = tmp_path / "custom.db"
    r = runner.invoke(
        app, ["run", "--fixture", str(fix), "--condition", "society", "--db", str(db)]
    )
    assert r.exit_code == 0

    v = runner.invoke(app, ["verify-log", str(db)])
    assert "I2.crew_duty" not in v.output  # domain check silently skipped
    assert "overall:" in v.output


def test_bench_prints_table():
    r = runner.invoke(app, ["bench", "--seeds", "7,1"])
    assert r.exit_code == 0
    assert "Full society" in r.stdout
    assert "Protected pax stranded" in r.stdout
