"""Regulation library: passages, resolver, embedding retrieval."""

from __future__ import annotations

from tarmac_society.canonical import sha256_hex
from tarmac_society.qwen.citations import Passage, RegLibrary
from tarmac_society.tarmac.regs import PASSAGES, build_reg_library


def _embed(texts):
    # tiny deterministic embed for retrieval tests
    from tarmac_society.qwen.transport import _hash_embed

    return [_hash_embed(t) for t in texts]


def test_passage_ids_unique_and_expected_count():
    ids = [p.id for p in PASSAGES]
    assert len(ids) == len(set(ids))
    assert {"far117.11", "dot-259.4", "duty_table.B", "med-policy.2"} <= set(ids)


def test_passage_sha256_matches_text():
    p = PASSAGES[0]
    assert p.sha256 == sha256_hex(p.text)
    d = p.as_dict()
    assert set(d) == {"id", "title", "text", "sha256"}


def test_library_get_and_resolver():
    lib = build_reg_library()
    assert lib.get("far117.11").id == "far117.11"
    assert lib.get("nope") is None
    resolve = lib.resolver()
    assert resolve("dot-259.4")["sha256"] == lib.get("dot-259.4").sha256
    assert resolve("nope") is None


def test_duplicate_ids_rejected():
    import pytest

    with pytest.raises(ValueError):
        RegLibrary([Passage("x", "t", "a"), Passage("x", "t", "b")])


def test_retrieve_top_k_is_deterministic():
    lib = build_reg_library()
    a = lib.retrieve("crew duty period ferry limit", _embed, k=3)
    b = lib.retrieve("crew duty period ferry limit", _embed, k=3)
    assert [p.id for p in a] == [p.id for p in b]
    assert len(a) == 3


def test_retrieve_finds_relevant_passage():
    lib = build_reg_library()
    top = lib.retrieve(
        "unaccompanied minor nonstop escort curfew re-book", _embed, k=5
    )
    assert "um-policy.4" in {p.id for p in top}


def test_retrieve_handles_zero_vectors_without_crashing():
    """Cosine similarity must not divide by zero when a vector's norm is 0."""
    lib = RegLibrary([Passage("a", "t", "text a"), Passage("b", "t", "text b")])

    def zero_embed(texts):
        return [[0.0, 0.0, 0.0, 0.0] for _ in texts]

    top = lib.retrieve("anything", zero_embed, k=2)
    assert len(top) == 2  # both score 0.0 (no div-by-zero), deterministic tie-break by id
    assert [p.id for p in top] == ["a", "b"]
