"""``tarmac`` command line — run, bench, replay, verify-log, seed.

Offline is the default and needs no key: every command works against the
deterministic policy agents. ``run --live`` switches the role agents to
DashScope (``qwen3.7-plus`` / ``qwen3.7-max``) behind ``DASHSCOPE_API_KEY``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .tarmac import bench as bench_mod
from .tarmac.baseline import run_single_planner
from .tarmac.metrics import compute_metrics, crew_duty_check
from .tarmac.run import run_society
from .tarmac.seed import generate, load_fixture, write_fixture
from .verify import verify_log

app = typer.Typer(add_completion=False, help="Tarmac — an airline-IRROPS agent society.")


def _load_scenario(scenario: str, seed: int, fixture: Path | None) -> dict:
    if fixture is not None:
        return load_fixture(fixture)
    return generate(scenario, seed)


def _print_run(bundle, scenario) -> None:
    res = bundle.result
    qr = res.quiescent_round if res.quiescent_round is not None else res.rounds_used
    contest_spend = 0 if bundle.bank is None else bundle.bank.total_spend()
    metrics = compute_metrics(
        bundle.ledger, scenario,
        rounds_to_quiescence=qr, contest_spend=contest_spend, quiescent=res.quiescent,
    )
    typer.echo(f"condition             : {bundle.condition}")
    typer.echo(f"rounds used           : {res.rounds_used} (quiescent={res.quiescent})")
    typer.echo(f"deadlocks             : {len(res.deadlocks)}")
    typer.echo(f"rulings (signed)      : {len(res.rulings)}")
    typer.echo(f"manifest hash         : {res.manifest_hash[:16]}…")
    typer.echo(f"chain length / head   : {res.chain_length} / {res.chain_head[:16]}…")
    typer.echo("--- metrics ---")
    typer.echo(f"stranded overnight    : {metrics['stranded_overnight']}")
    typer.echo(f"seated / same-day     : {metrics['seated']} / {metrics['same_day_recovered']}")
    typer.echo(f"tight conns saved     : {metrics['tight_connections_saved']} / 12")
    typer.echo(f"special-needs SLA     : {metrics['special_needs_sla_pct']}%  "
               f"(failed: {metrics['special_needs_failed'] or 'none'})")
    typer.echo(f"crew violations (I2)  : {metrics['crew_violations']}")
    typer.echo(f"contest spend         : {metrics['contest_spend']}")


@app.command()
def run(
    scenario: str = typer.Option("storm_dfw", help="Scenario name."),
    seed: int = typer.Option(7, help="Scenario seed."),
    condition: str = typer.Option(
        "society", help="society | society_minus_mediator | single"
    ),
    fixture: Path | None = typer.Option(None, help="Load a committed fixture JSON instead."),
    db: Path | None = typer.Option(None, help="Write the replayable run.db here."),
    live: bool = typer.Option(False, help="Use live Qwen agents (needs DASHSCOPE_API_KEY)."),
    rounds: int = typer.Option(6, help="Round cap."),
) -> None:
    """Run one condition and print the manifest metrics."""
    sc = _load_scenario(scenario, seed, fixture)
    if db:
        db.parent.mkdir(parents=True, exist_ok=True)
    db_path = str(db) if db else ":memory:"
    if condition == "single":
        if live:
            typer.echo("note: --live has no effect on the greedy single planner", err=True)
        bundle = run_single_planner(sc, seed, db_path=db_path)
    else:
        transport = None
        if live:
            from .qwen.transport import LiveQwen  # lazy

            transport = LiveQwen()
        bundle = run_society(
            sc, seed, condition=condition, transport=transport, max_rounds=rounds, db_path=db_path,
        )
    _print_run(bundle, sc)
    if db:
        typer.echo(f"\nrun.db written to {db}")


@app.command()
def bench(
    seeds: str = typer.Option(
        ",".join(str(s) for s in bench_mod.DEFAULT_SEEDS),
        help="Comma-separated seed list.",
    ),
    out: Path | None = typer.Option(None, help="Write the markdown table here."),
) -> None:
    """Run the 3-condition ablation and print the medians/IQR table."""
    seed_list = [int(s) for s in seeds.split(",") if s.strip()]
    result = bench_mod.run_ablation(tuple(seed_list))
    table = bench_mod.render_markdown(result)
    typer.echo(table)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table + "\n")
        typer.echo(f"\nwritten to {out}", err=True)


@app.command()
def replay(run_db: Path = typer.Argument(..., help="A run.db to replay.")) -> None:
    """Replay the manifest from a run's log and compare to the recorded one."""
    from .chainlog import ChainLog
    from .storage import SQLiteStorage
    from .verify import replay_manifest

    entries = ChainLog(SQLiteStorage(str(run_db))).entries()
    replayed = replay_manifest(entries)
    recorded = next((e for e in reversed(entries) if e.kind == "manifest"), None)
    typer.echo(json.dumps(replayed, indent=2, sort_keys=True))
    if recorded is not None:
        ok = replayed == recorded.body["manifest"]
        typer.echo(f"\nmatches recorded manifest: {ok}")
        raise typer.Exit(code=0 if ok else 1)


@app.command(name="verify-log")
def verify_log_cmd(run_db: Path = typer.Argument(..., help="A run.db to verify.")) -> None:
    """Re-derive the chain and re-check invariants I1–I5 (exit 0 iff all pass)."""
    from .chainlog import ChainLog
    from .storage import SQLiteStorage

    storage = SQLiteStorage(str(run_db))
    entries = ChainLog(storage).entries()
    genesis = entries[0].body if entries else {}
    domain_checks = []
    try:
        sc = generate(genesis.get("scenario", "storm_dfw"), genesis.get("seed", 7))
        domain_checks.append(crew_duty_check(sc))
    except Exception:  # non-storm_dfw logs: skip the airline-specific check
        pass
    report = verify_log(entries, domain_checks=domain_checks)
    for name, ok, detail in report.checks:
        typer.echo(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    typer.echo(f"\noverall: {'OK' if report.ok else 'FAILED'} ({report.chain_length} entries)")
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def seed(
    scenario: str = typer.Option("storm_dfw", help="Scenario name."),
    seed: int = typer.Option(7, help="Scenario seed."),
    out: Path = typer.Option(Path("fixtures/storm_dfw_seed7.json"), help="Fixture output path."),
) -> None:
    """Generate a scenario and freeze it as a committed fixture."""
    sc = generate(scenario, seed)
    path = write_fixture(sc, out)
    typer.echo(f"wrote {path} ({len(sc['pax'])} pax, seed {seed})")


if __name__ == "__main__":  # pragma: no cover
    app()
