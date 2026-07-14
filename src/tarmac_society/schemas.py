"""Protocol schemas: Claim, Position, Ruling (+ deadlocks, views).

These are the *wire types* of the society. In live mode the Qwen models are
prompted with the JSON schema of these classes and their output is validated
here (one reject-and-retry). In offline mode the deterministic policy agents
construct them directly — same types, same ledger, same physics.

Claim state machine (formalized in docs/SPEC-CLAIMS.md):

    proposed -> committed -> revealed -> granted | blocked | reveal_rejected
    granted  -> revoked | released | voided
    blocked  -> granted (by ruling) | voided | withdrawn
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ClaimStatus",
    "ClaimProposal",
    "ClaimRecord",
    "Position",
    "RulingOp",
    "Ruling",
    "SignedRuling",
    "Deadlock",
]


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    COMMITTED = "committed"
    REVEALED = "revealed"
    GRANTED = "granted"
    BLOCKED = "blocked"
    REVEAL_REJECTED = "reveal_rejected"
    REVOKED = "revoked"
    RELEASED = "released"
    VOIDED = "voided"
    WITHDRAWN = "withdrawn"


class ClaimProposal(BaseModel):
    """What an agent asks for: ``qty`` units of ``resource`` for named beneficiaries."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    resource: str
    qty: int = Field(gt=0)
    beneficiaries: list[str] = Field(min_length=1)
    basis: str = Field(min_length=1, description="why the agent believes it is entitled")
    revocable: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _qty_matches(self) -> ClaimProposal:
        if len(self.beneficiaries) != self.qty:
            raise ValueError("qty must equal len(beneficiaries)")
        if len(set(self.beneficiaries)) != len(self.beneficiaries):
            raise ValueError("duplicate beneficiaries in one claim")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """The exact dict that gets sealed in a commitment."""
        return self.model_dump(mode="json")


class ClaimRecord(BaseModel):
    """Ledger-side view of a claim after reveal."""

    model_config = ConfigDict(extra="forbid")

    id: str
    proposal: ClaimProposal
    status: ClaimStatus
    round_committed: int
    round_resolved: int | None = None
    commitment_id: str | None = None
    holders: list[str] = Field(default_factory=list, description="beneficiaries currently allocated")


class Position(BaseModel):
    """A structured position paper on a contested/target claim.

    ``stance='block'`` opens a formal *contest* — it costs credibility
    (see currency.py) and must cite at least one regulation/policy passage.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str
    stance: Literal["block", "support", "yield"]
    target_claim: str
    argument: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)
    concession: str | None = None
    release: list[str] | None = Field(
        default=None,
        description="stance='yield' on one's own claim: beneficiaries to give back (None = all)",
    )

    @model_validator(mode="after")
    def _block_needs_citation(self) -> Position:
        if self.stance == "block" and not self.citations:
            raise ValueError("a blocking position must cite at least one source")
        return self


class RulingOp(BaseModel):
    """A typed ledger mutation a ruling orders. Applied atomically, in order."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["revoke", "grant", "void"]
    claim_id: str
    beneficiaries: list[str] | None = Field(
        default=None,
        description="subset for partial revoke/grant; None = all in the claim",
    )


class Ruling(BaseModel):
    """A binding mediator decision. Must cite >=1 source (invariant I3)."""

    model_config = ConfigDict(extra="forbid")

    deadlock_id: str
    decision: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    citations: list[str] = Field(min_length=1)
    ops: list[RulingOp] = Field(default_factory=list)
    winners: list[str] = Field(default_factory=list)
    losers: list[str] = Field(default_factory=list)

    @field_validator("citations")
    @classmethod
    def _nonempty_citations(cls, v: list[str]) -> list[str]:
        if not any(c.strip() for c in v):
            raise ValueError("citations must be non-empty strings")
        return v


class SignedRuling(BaseModel):
    """Ruling body + citation-passage hashes, Ed25519-signed by the orchestrator."""

    model_config = ConfigDict(extra="forbid")

    ruling_id: str
    round: int
    body: Ruling
    citation_hashes: dict[str, str] = Field(
        default_factory=dict, description="citation id -> sha256 of the cited passage text"
    )
    signature: str
    signer_public_hex: str

    def signable_body(self) -> dict[str, Any]:
        """The exact dict the Ed25519 signature covers."""
        return {
            "ruling_id": self.ruling_id,
            "round": self.round,
            "body": self.body.model_dump(mode="json"),
            "citation_hashes": dict(sorted(self.citation_hashes.items())),
        }


class Deadlock(BaseModel):
    """A mechanically detected negotiation deadlock."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["cycle", "contested"]
    round: int
    resources: list[str]
    agents: list[str]
    claims: list[str]
    detail: str = ""
