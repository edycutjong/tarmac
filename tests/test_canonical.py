"""Canonical JSON + hashing determinism."""

from __future__ import annotations

import math

import pytest

from tarmac_society.canonical import canonical_bytes, canonical_json, hash_obj, sha256_hex


def test_key_order_is_irrelevant():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_is_compact_and_sorted():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_bytes_is_utf8_of_text():
    obj = {"x": [1, 2, 3]}
    assert canonical_bytes(obj) == canonical_json(obj).encode("utf-8")


def test_ensure_ascii_escapes_unicode():
    text = canonical_json({"k": "café"})
    assert "\\u" in text and all(ord(c) < 128 for c in text)


def test_nan_and_infinity_are_rejected():
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})
    with pytest.raises(ValueError):
        canonical_json({"x": math.inf})


def test_sha256_hex_accepts_str_and_bytes():
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("abc")) == 64


def test_hash_obj_is_stable_across_key_order():
    assert hash_obj({"a": 1, "b": 2}) == hash_obj({"b": 2, "a": 1})


def test_hash_obj_changes_with_content():
    assert hash_obj({"a": 1}) != hash_obj({"a": 2})


def test_nested_structures_hash_stably():
    a = {"list": [{"z": 1, "a": 2}], "n": 3}
    b = {"n": 3, "list": [{"a": 2, "z": 1}]}
    assert hash_obj(a) == hash_obj(b)
