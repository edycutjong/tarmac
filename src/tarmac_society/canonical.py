"""Canonical JSON encoding + hashing.

Every hashed artifact in tarmac-society (commitments, chain-log entries,
ruling bodies, manifests) goes through ``canonical_json`` so that two
semantically identical objects always hash identically, regardless of dict
insertion order or unicode representation.

Rules:
- keys sorted, compact separators, ``ensure_ascii=True``
- NaN/Infinity rejected (``allow_nan=False``)
- only JSON-native types are accepted (no datetimes, no bytes) — callers
  convert first, which keeps hashes stable across Python versions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["canonical_json", "canonical_bytes", "sha256_hex", "hash_obj"]


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text for ``obj`` (sorted keys, compact, ASCII)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    """UTF-8 bytes of the canonical JSON text."""
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 hex digest of raw bytes (or UTF-8 of a string)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """SHA-256 hex digest of the canonical JSON encoding of ``obj``."""
    return sha256_hex(canonical_bytes(obj))
