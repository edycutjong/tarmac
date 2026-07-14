"""SQLiteStorage: execute/query, IntegrityViolation, transaction semantics."""

from __future__ import annotations

import pytest

from tarmac_society.storage import IntegrityViolation, SQLiteStorage

DDL = "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)"


def test_executescript_and_query():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    st.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    assert st.query("SELECT v FROM t WHERE id=?", (1,)) == [("a",)]


def test_integrity_violation_maps_from_sqlite_error():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    st.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    with pytest.raises(IntegrityViolation):
        st.execute("INSERT INTO t(id, v) VALUES (1, 'b')")  # dup primary key


def test_transaction_commits_on_success():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    with st.transaction():
        st.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    assert st.query("SELECT COUNT(*) FROM t")[0][0] == 1


def test_transaction_rolls_back_on_exception():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    with pytest.raises(RuntimeError):
        with st.transaction():
            st.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
            raise RuntimeError("boom")
    assert st.query("SELECT COUNT(*) FROM t")[0][0] == 0


def test_nested_transaction_is_reentrant():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    with st.transaction():
        st.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
        with st.transaction():
            st.execute("INSERT INTO t(id, v) VALUES (2, 'b')")
    assert st.query("SELECT COUNT(*) FROM t")[0][0] == 2


def test_close_is_idempotentish():
    st = SQLiteStorage(":memory:")
    st.executescript(DDL)
    st.close()
