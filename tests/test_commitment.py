"""Sealed-bid commitments: SHA256(claim || nonce), commit -> reveal, mismatch."""

from __future__ import annotations

from random import Random

import pytest

from tarmac_society.commitment import (
    NONCE_BYTES,
    Commitment,
    commitment_digest,
    make_nonce,
    verify_commitment,
)

CLAIM = {"agent": "advocate", "resource": "seat:QW441", "qty": 3, "beneficiaries": ["a", "b", "c"]}


def test_nonce_is_128_bit_hex():
    nonce = make_nonce()
    assert len(bytes.fromhex(nonce)) == NONCE_BYTES == 16


def test_seeded_nonce_is_reproducible():
    assert make_nonce(Random("s")) == make_nonce(Random("s"))
    assert make_nonce(Random("s")) != make_nonce(Random("t"))


def test_digest_is_deterministic_and_hex():
    d1 = commitment_digest(CLAIM, "00" * 16)
    d2 = commitment_digest(CLAIM, "00" * 16)
    assert d1 == d2 and len(d1) == 64


def test_digest_depends_on_nonce():
    assert commitment_digest(CLAIM, "00" * 16) != commitment_digest(CLAIM, "11" * 16)


def test_digest_depends_on_claim():
    other = dict(CLAIM, qty=2, beneficiaries=["a", "b"])
    assert commitment_digest(CLAIM, "00" * 16) != commitment_digest(other, "00" * 16)


def test_digest_is_order_independent_in_claim():
    reordered = {k: CLAIM[k] for k in reversed(list(CLAIM))}
    assert commitment_digest(CLAIM, "0f" * 16) == commitment_digest(reordered, "0f" * 16)


def test_wrong_nonce_length_raises():
    with pytest.raises(ValueError):
        commitment_digest(CLAIM, "00" * 8)  # 8 bytes, not 16


def test_verify_commitment_true_on_match():
    nonce = make_nonce()
    digest = commitment_digest(CLAIM, nonce)
    assert verify_commitment(CLAIM, nonce, digest) is True


def test_verify_commitment_false_on_tamper():
    nonce = make_nonce()
    digest = commitment_digest(CLAIM, nonce)
    tampered = dict(CLAIM, qty=2, beneficiaries=["a", "b"])
    assert verify_commitment(tampered, nonce, digest) is False


def test_verify_commitment_false_on_bad_nonce_length():
    assert verify_commitment(CLAIM, "abcd", "00" * 32) is False


def test_commitment_seal_and_matches_roundtrip():
    commitment, nonce = Commitment.seal("advocate", CLAIM)
    assert commitment.agent == "advocate"
    assert commitment.matches(CLAIM, nonce) is True


def test_commitment_does_not_match_altered_claim():
    commitment, nonce = Commitment.seal("advocate", CLAIM, rng=Random(1))
    assert commitment.matches(dict(CLAIM, qty=1, beneficiaries=["a"]), nonce) is False


def test_seal_is_reproducible_with_seeded_rng():
    c1, n1 = Commitment.seal("a", CLAIM, rng=Random(7))
    c2, n2 = Commitment.seal("a", CLAIM, rng=Random(7))
    assert (c1.digest, n1) == (c2.digest, n2)
