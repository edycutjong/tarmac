"""Ed25519 signing for mediator rulings (pynacl).

Every binding ``Ruling`` is signed by the orchestrator's Ed25519 key over the
canonical JSON of the ruling body (which itself embeds the SHA-256 hashes of
the regulation passages it cites). Rulings are therefore portable artifacts:
anyone holding the public key can verify *what was decided and on what basis*
without trusting the run database.

Offline/demo runs derive a deterministic keypair from the run seed so replays
are reproducible; production keeps the signing key in the environment
(``TARMAC_SIGNING_KEY_HEX``) and commits only the public key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .canonical import canonical_bytes

__all__ = ["KeyPair", "generate_keypair", "keypair_from_seed", "sign_body", "verify_body"]


@dataclass(frozen=True)
class KeyPair:
    signing_key: SigningKey

    @property
    def public_hex(self) -> str:
        return self.signing_key.verify_key.encode().hex()

    @property
    def private_hex(self) -> str:
        return self.signing_key.encode().hex()


def generate_keypair() -> KeyPair:
    """Fresh random keypair (production path)."""
    return KeyPair(SigningKey.generate())


def keypair_from_seed(seed: bytes | str) -> KeyPair:
    """Deterministic keypair from an arbitrary seed (offline/demo path).

    The 32-byte Ed25519 seed is SHA-256 of the input, so any string works.
    """
    if isinstance(seed, str):
        seed = seed.encode("utf-8")
    return KeyPair(SigningKey(hashlib.sha256(seed).digest()))


def keypair_from_private_hex(private_hex: str) -> KeyPair:
    return KeyPair(SigningKey(bytes.fromhex(private_hex)))


def sign_body(body: Mapping[str, Any], keypair: KeyPair) -> str:
    """Sign canonical JSON of ``body``; returns detached signature hex."""
    sig = keypair.signing_key.sign(canonical_bytes(dict(body))).signature
    return sig.hex()


def verify_body(body: Mapping[str, Any], signature_hex: str, public_hex: str) -> bool:
    """True iff ``signature_hex`` is a valid Ed25519 signature of ``body``."""
    try:
        VerifyKey(bytes.fromhex(public_hex)).verify(
            canonical_bytes(dict(body)), bytes.fromhex(signature_hex)
        )
        return True
    except (BadSignatureError, ValueError):
        return False
