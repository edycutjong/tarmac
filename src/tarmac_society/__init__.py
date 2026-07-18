"""tarmac-society — a domain-agnostic claim-ledger protocol for agent societies.

The reusable spine of the Tarmac submission. Agents negotiate over
capacity-constrained resources through **revocable claims on a shared,
row-locked ledger**; contested rounds use **sealed-bid commitments**
(``SHA256(claim || nonce)``); a mechanical **deadlock detector** (wait-for
cycles / contested streaks) triggers a **mediator** whose binding rulings are
**Ed25519-signed** and must cite a source; every event extends a
**hash-chained decision log** that ``verify_log`` re-derives and re-checks
against invariants I1–I5.

Nothing in this top-level package is airline-specific — the airline scenario
(``storm_dfw``) lives in :mod:`tarmac_society.tarmac`, and a 20-line
meeting-rooms reuse lives in ``examples/``.
"""

from __future__ import annotations

from .canonical import canonical_bytes, canonical_json, hash_obj, sha256_hex
from .chainlog import ChainLog, LogEntry, verify_chain
from .commitment import (
    Commitment,
    commitment_digest,
    make_nonce,
    verify_commitment,
)
from .currency import CredibilityBank, CurrencyError
from .deadlock import DeadlockDetector, find_cycles
from .ledger import ClaimLedger, LedgerError
from .mediator import Agent, AgentView, Mediator, MediatorView
from .schemas import (
    ClaimProposal,
    ClaimRecord,
    ClaimStatus,
    Deadlock,
    Position,
    Ruling,
    RulingOp,
    SignedRuling,
)
from .signing import (
    KeyPair,
    generate_keypair,
    keypair_from_seed,
    sign_body,
    verify_body,
)
from .society import CitationResolver, ProtocolError, RunResult, Society
from .storage import IntegrityViolation, SQLiteStorage, Storage
from .verify import VerifyReport, replay_manifest, verify_log

__version__ = "1.1.1"

__all__ = [
    "__version__",
    # canonical / hashing
    "canonical_json",
    "canonical_bytes",
    "sha256_hex",
    "hash_obj",
    # commitments
    "Commitment",
    "commitment_digest",
    "make_nonce",
    "verify_commitment",
    # signing
    "KeyPair",
    "generate_keypair",
    "keypair_from_seed",
    "sign_body",
    "verify_body",
    # storage
    "Storage",
    "SQLiteStorage",
    "IntegrityViolation",
    # schemas
    "ClaimProposal",
    "ClaimRecord",
    "ClaimStatus",
    "Position",
    "Ruling",
    "RulingOp",
    "SignedRuling",
    "Deadlock",
    # ledger / currency / deadlock
    "ClaimLedger",
    "LedgerError",
    "CredibilityBank",
    "CurrencyError",
    "DeadlockDetector",
    "find_cycles",
    # mediation / society
    "Agent",
    "AgentView",
    "Mediator",
    "MediatorView",
    "Society",
    "RunResult",
    "CitationResolver",
    "ProtocolError",
    # chain log / verification
    "ChainLog",
    "LogEntry",
    "verify_chain",
    "VerifyReport",
    "replay_manifest",
    "verify_log",
]
