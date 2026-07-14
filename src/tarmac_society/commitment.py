"""Sealed-bid commitments: ``SHA256(canonical_claim || nonce_128)``.

Agents *commit* to a claim before anyone reveals, then *reveal* the claim
plus the nonce. The ledger recomputes the digest and rejects any reveal that
does not match its commitment (invariant **I4**). This prevents an agent
from adapting its bid after seeing a rival's, and makes contested rounds
provably leak-free.

The nonce is exactly 16 raw bytes (128 bits). Because the nonce length is
fixed, ``canonical || nonce`` is an unambiguous encoding.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from random import Random
from typing import Any

from .canonical import canonical_bytes

__all__ = ["NONCE_BYTES", "Commitment", "make_nonce", "commitment_digest", "verify_commitment"]

NONCE_BYTES = 16  # 128-bit


def make_nonce(rng: Random | None = None) -> str:
    """Return a fresh 128-bit nonce as hex.

    ``rng`` (a seeded ``random.Random``) makes offline runs reproducible;
    live runs pass ``None`` and get ``secrets.token_bytes``.
    """
    if rng is None:
        return secrets.token_bytes(NONCE_BYTES).hex()
    return rng.getrandbits(NONCE_BYTES * 8).to_bytes(NONCE_BYTES, "big").hex()


def commitment_digest(claim: Mapping[str, Any], nonce_hex: str) -> str:
    """SHA256(canonical_json(claim) || nonce_bytes) as hex."""
    nonce = bytes.fromhex(nonce_hex)
    if len(nonce) != NONCE_BYTES:
        raise ValueError(f"nonce must be exactly {NONCE_BYTES} bytes, got {len(nonce)}")
    return hashlib.sha256(canonical_bytes(dict(claim)) + nonce).hexdigest()


def verify_commitment(claim: Mapping[str, Any], nonce_hex: str, digest_hex: str) -> bool:
    """True iff the claim+nonce re-derive ``digest_hex`` (constant-time compare)."""
    try:
        derived = commitment_digest(claim, nonce_hex)
    except ValueError:
        return False
    # hashlib digests are fixed length; use compare_digest anyway.
    import hmac

    return hmac.compare_digest(derived, digest_hex)


@dataclass(frozen=True)
class Commitment:
    """A sealed bid: the digest is public at commit time; claim+nonce stay private."""

    agent: str
    digest: str

    @classmethod
    def seal(
        cls, agent: str, claim: Mapping[str, Any], rng: Random | None = None
    ) -> tuple[Commitment, str]:
        """Seal ``claim``; returns ``(commitment, nonce_hex)``.

        The caller keeps ``nonce_hex`` (and the claim) secret until reveal.
        """
        nonce_hex = make_nonce(rng)
        return cls(agent=agent, digest=commitment_digest(claim, nonce_hex)), nonce_hex

    def matches(self, claim: Mapping[str, Any], nonce_hex: str) -> bool:
        return verify_commitment(claim, nonce_hex, self.digest)
