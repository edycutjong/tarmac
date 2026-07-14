"""Ed25519 signing of ruling bodies."""

from __future__ import annotations

from tarmac_society.signing import (
    generate_keypair,
    keypair_from_private_hex,
    keypair_from_seed,
    sign_body,
    verify_body,
)

BODY = {"deadlock_id": "d-01", "decision": "grant advocate", "citations": ["dot-259.4"]}


def test_generate_keypair_has_hex_material():
    kp = generate_keypair()
    assert len(bytes.fromhex(kp.public_hex)) == 32
    assert len(bytes.fromhex(kp.private_hex)) == 32


def test_sign_then_verify_roundtrip():
    kp = generate_keypair()
    sig = sign_body(BODY, kp)
    assert verify_body(BODY, sig, kp.public_hex) is True


def test_verify_fails_on_tampered_body():
    kp = generate_keypair()
    sig = sign_body(BODY, kp)
    assert verify_body(dict(BODY, decision="grant rebooking"), sig, kp.public_hex) is False


def test_verify_fails_on_wrong_key():
    kp, other = generate_keypair(), generate_keypair()
    sig = sign_body(BODY, kp)
    assert verify_body(BODY, sig, other.public_hex) is False


def test_verify_fails_on_garbage_signature():
    kp = generate_keypair()
    assert verify_body(BODY, "not-hex", kp.public_hex) is False
    assert verify_body(BODY, "00" * 64, kp.public_hex) is False


def test_keypair_from_seed_is_deterministic():
    a = keypair_from_seed("run-7")
    b = keypair_from_seed("run-7")
    assert a.public_hex == b.public_hex
    assert keypair_from_seed("run-7").public_hex != keypair_from_seed("run-8").public_hex


def test_keypair_from_seed_accepts_bytes_and_str():
    assert keypair_from_seed(b"x").public_hex == keypair_from_seed("x").public_hex


def test_signature_is_order_independent_over_body():
    kp = keypair_from_seed("k")
    sig = sign_body({"a": 1, "b": 2}, kp)
    assert verify_body({"b": 2, "a": 1}, sig, kp.public_hex) is True


def test_roundtrip_via_private_hex():
    kp = generate_keypair()
    restored = keypair_from_private_hex(kp.private_hex)
    assert restored.public_hex == kp.public_hex
    sig = sign_body(BODY, restored)
    assert verify_body(BODY, sig, kp.public_hex) is True
