"""replay_manifest + verify_log (I1, I3, I4, I5 + domain checks)."""

from __future__ import annotations

from random import Random

from tarmac_society import ChainLog, LogEntry, SQLiteStorage, replay_manifest, verify_log
from tarmac_society.canonical import hash_obj
from tarmac_society.commitment import commitment_digest, make_nonce
from tarmac_society.tarmac.baseline import run_single_planner
from tarmac_society.tarmac.metrics import crew_duty_check
from tarmac_society.tarmac.run import run_society
from tarmac_society.verify import VerifyReport


def test_replay_manifest_from_alloc_dealloc_stream():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})
    log.append("alloc", {"resource": "seat:A", "beneficiary": "p1", "claim_id": "c-1"}, 1)
    log.append("alloc", {"resource": "seat:A", "beneficiary": "p2", "claim_id": "c-1"}, 1)
    log.append("dealloc", {"resource": "seat:A", "beneficiary": "p1", "claim_id": "c-1"}, 2)
    assert replay_manifest(log.entries()) == {"seat:A": ["p2"]}


def test_replay_matches_society_manifest(scenario):
    b = run_society(scenario, 7, condition="society")
    assert replay_manifest(b.chainlog.entries()) == b.result.manifest


def test_verify_log_society_all_pass(scenario):
    b = run_society(scenario, 7, condition="society")
    report = verify_log(b.chainlog.entries(), domain_checks=[crew_duty_check(scenario)])
    assert report.ok, report.failures()
    names = {n for n, _ok, _d in report.checks}
    assert {"chain", "I5.replay", "I1.capacity", "I1.exclusivity",
            "I3.rulings_cite_source", "I4.accepted_reveals_match", "I2.crew_duty"} <= names


def test_verify_log_reads_persisted_db(scenario, tmp_path):
    db = str(tmp_path / "run.db")
    run_society(scenario, 7, condition="society", db_path=db)
    report = verify_log(db, domain_checks=[crew_duty_check(scenario)])
    assert report.ok


def test_single_planner_fails_crew_duty_invariant(scenario):
    b = run_single_planner(scenario, 7)
    report = verify_log(b.chainlog.entries(), domain_checks=[crew_duty_check(scenario)])
    i2 = [c for c in report.checks if c[0] == "I2.crew_duty"][0]
    assert i2[1] is False  # single planner leaves a duty-illegal crew
    assert report.ok is False


def test_verify_log_detects_tampered_chain(scenario):
    b = run_society(scenario, 7, condition="society")
    entries = b.chainlog.entries()
    # corrupt a mid-chain body
    i = len(entries) // 2
    e = entries[i]
    entries[i] = LogEntry(e.seq, e.round, e.kind, dict(e.body, _x=1), e.prev_hash, e.hash)
    report = verify_log(entries)
    assert report.ok is False
    assert any(n == "chain" and not ok for n, ok, _ in report.checks)


def test_verify_report_as_dict(scenario):
    b = run_society(scenario, 7, condition="society")
    d = verify_log(b.chainlog.entries()).as_dict()
    assert d["ok"] is True and isinstance(d["checks"], list)


def test_verify_report_failures_lists_only_failed_checks():
    report = VerifyReport(ok=True)
    report.add("a", True, "fine")
    report.add("b", False, "broken")
    report.add("c", True, "also fine")
    assert [n for n, _ok, _d in report.failures()] == ["b"]


def test_verify_log_accepts_a_chainlog_instance_directly():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})
    log.append("manifest", {"manifest": {}, "manifest_hash": hash_obj({})}, 1)
    report = verify_log(log)  # a ChainLog, not .entries() nor a path
    assert report.chain_length == 2
    assert report.ok is True


def test_verify_log_detects_exclusivity_clash():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})
    log.append("resource", {"id": "seat:A", "capacity": 1, "group": "seat"}, 0)
    log.append("resource", {"id": "seat:B", "capacity": 1, "group": "seat"}, 0)
    log.append("alloc", {"resource": "seat:A", "beneficiary": "p1", "claim_id": "c-1"}, 1)
    log.append("alloc", {"resource": "seat:B", "beneficiary": "p1", "claim_id": "c-2"}, 1)
    manifest = {"seat:A": ["p1"], "seat:B": ["p1"]}
    log.append("manifest", {"manifest": manifest, "manifest_hash": hash_obj(manifest)}, 1)

    report = verify_log(log.entries())
    excl = next(c for c in report.checks if c[0] == "I1.exclusivity")
    assert excl[1] is False
    assert "p1 holds" in excl[2]


def test_verify_log_detects_forged_reveal_entries():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})
    claim = {"agent": "a", "resource": "seat:A", "qty": 1, "beneficiaries": ["p1"], "basis": "x"}

    # a "reveal_ok" whose logged claim/nonce do NOT re-derive its commitment digest
    nonce1 = make_nonce(Random(0))
    digest1 = commitment_digest(claim, nonce1)
    log.append("commit", {"commitment_id": "m-1", "agent": "a", "digest": digest1}, 1)
    tampered = dict(claim, qty=2, beneficiaries=["p1", "p2"])
    log.append(
        "reveal_ok",
        {"commitment_id": "m-1", "claim_id": "c-1", "agent": "a", "claim": tampered, "nonce": nonce1},
        1,
    )

    # a "reveal_reject" whose logged claim/nonce actually DO match — a false rejection
    nonce2 = make_nonce(Random(1))
    digest2 = commitment_digest(claim, nonce2)
    log.append("commit", {"commitment_id": "m-2", "agent": "a", "digest": digest2}, 1)
    log.append(
        "reveal_reject",
        {"commitment_id": "m-2", "agent": "a", "claim": claim, "nonce": nonce2,
         "expected_digest": digest2},
        1,
    )

    manifest: dict = {}
    log.append("manifest", {"manifest": manifest, "manifest_hash": hash_obj(manifest)}, 1)

    report = verify_log(log.entries())
    accept_check = next(c for c in report.checks if c[0] == "I4.accepted_reveals_match")
    reject_check = next(c for c in report.checks if c[0] == "I4.rejected_reveals_mismatch")
    assert accept_check[1] is False and "m-1" in accept_check[2]
    assert reject_check[1] is False and "m-2" in reject_check[2]


def test_verify_log_detects_uncited_ruling():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})
    log.append("ruling", {"ruling_id": "r-1", "body": {"citations": []}}, 1)
    manifest: dict = {}
    log.append("manifest", {"manifest": manifest, "manifest_hash": hash_obj(manifest)}, 1)

    report = verify_log(log.entries())
    cite_check = next(c for c in report.checks if c[0] == "I3.rulings_cite_source")
    assert cite_check[1] is False
    assert "r-1" in cite_check[2]


def test_verify_log_flags_missing_manifest_entry():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t"})  # no "manifest" entry ever appended
    report = verify_log(log.entries())
    i5 = next(c for c in report.checks if c[0] == "I5.replay")
    assert i5[1] is False
    assert "no manifest entry" in i5[2]
