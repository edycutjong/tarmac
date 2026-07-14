"""Hash-chained decision log + verify_chain tamper detection."""

from __future__ import annotations

import pytest

from tarmac_society import ChainLog, SQLiteStorage, verify_chain
from tarmac_society.chainlog import GENESIS_KIND, LogEntry


def _fresh_log():
    log = ChainLog(SQLiteStorage(":memory:"))
    log.genesis({"scenario": "t", "seed": 0})
    return log


def test_genesis_then_append_grows_chain():
    log = _fresh_log()
    assert log.length == 1
    log.append("event", {"x": 1}, 1)
    assert log.length == 2


def test_genesis_twice_raises():
    log = _fresh_log()
    with pytest.raises(RuntimeError):
        log.genesis({"scenario": "t"})


def test_append_before_genesis_raises():
    log = ChainLog(SQLiteStorage(":memory:"))
    with pytest.raises(RuntimeError):
        log.append("event", {"x": 1}, 0)


def test_head_changes_on_append():
    log = _fresh_log()
    h0 = log.head
    log.append("event", {"x": 1}, 1)
    assert log.head != h0 and len(log.head) == 64


def test_verify_chain_ok():
    log = _fresh_log()
    for i in range(5):
        log.append("e", {"i": i}, i)
    ok, detail = verify_chain(log.entries())
    assert ok, detail


def test_verify_chain_detects_body_tamper():
    log = _fresh_log()
    log.append("e", {"i": 1}, 1)
    entries = log.entries()
    tampered = entries[:]
    e = tampered[1]
    tampered[1] = LogEntry(e.seq, e.round, e.kind, {"i": 999}, e.prev_hash, e.hash)
    ok, detail = verify_chain(tampered)
    assert not ok and "hash mismatch" in detail


def test_verify_chain_detects_broken_link():
    log = _fresh_log()
    log.append("e", {"i": 1}, 1)
    entries = log.entries()
    e = entries[1]
    entries[1] = LogEntry(e.seq, e.round, e.kind, e.body, "deadbeef" * 8, e.hash)
    ok, detail = verify_chain(entries)
    assert not ok and "broken link" in detail


def test_verify_chain_detects_sequence_gap():
    log = _fresh_log()
    log.append("e", {"i": 1}, 1)
    entries = log.entries()
    entries.pop(0)  # remove genesis -> gap / non-genesis first
    ok, _ = verify_chain(entries)
    assert not ok


def test_verify_chain_empty_is_not_ok():
    ok, detail = verify_chain([])
    assert not ok and "empty" in detail


def test_first_entry_must_be_genesis():
    log = ChainLog(SQLiteStorage(":memory:"))
    # forge a first non-genesis entry
    from tarmac_society.chainlog import _entry_hash  # type: ignore

    body = {"x": 1}
    h = _entry_hash("", 0, 0, "event", body)
    ok, detail = verify_chain([LogEntry(0, 0, "event", body, "", h)])
    assert not ok and "genesis" in detail


def test_entries_filter_by_kind():
    log = _fresh_log()
    log.append("alloc", {"a": 1}, 1)
    log.append("dealloc", {"a": 1}, 1)
    log.append("alloc", {"a": 2}, 2)
    assert len(log.entries("alloc")) == 2
    assert log.entries("alloc")[0].kind == "alloc"


def test_genesis_kind_constant():
    log = _fresh_log()
    assert log.entries()[0].kind == GENESIS_KIND


def test_two_identical_logs_have_identical_heads():
    a = _fresh_log()
    b = _fresh_log()
    for log in (a, b):
        log.append("e", {"v": 1}, 1)
        log.append("f", {"v": 2}, 2)
    assert a.head == b.head
