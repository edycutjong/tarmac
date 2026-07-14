"""Regulation-passage library with embedding retrieval.

Positions and rulings must cite sources (invariant I3). The library holds
passages (id, title, text) and retrieves the top-k for a query via cosine
similarity over embeddings supplied by the transport:

- **LiveQwen** embeds with ``text-embedding-v4``;
- **FakeQwen** embeds with deterministic feature hashing (same code path,
  zero network) — retrieval is reproducible offline.

Passage hashes (SHA-256 of the text) are what signed rulings embed, so a
ruling is verifiable against the exact passage text it stood on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..canonical import sha256_hex

__all__ = ["Passage", "RegLibrary"]


@dataclass(frozen=True)
class Passage:
    id: str
    title: str
    text: str

    @property
    def sha256(self) -> str:
        return sha256_hex(self.text)

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "text": self.text, "sha256": self.sha256}


class RegLibrary:
    def __init__(self, passages: list[Passage]) -> None:
        ids = [p.id for p in passages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate passage ids")
        self.passages = list(passages)
        self._by_id = {p.id: p for p in passages}
        self._vectors: dict[str, list[float]] | None = None

    def get(self, passage_id: str) -> Passage | None:
        return self._by_id.get(passage_id)

    def resolver(self):
        """A ``CitationResolver`` for ``Society`` (id -> dict or None)."""

        def _resolve(citation_id: str) -> dict[str, str] | None:
            p = self.get(citation_id)
            return p.as_dict() if p else None

        return _resolve

    # ------------------------------------------------------------- retrieval
    def _ensure_vectors(self, embed) -> None:
        if self._vectors is None:
            texts = [f"{p.title}\n{p.text}" for p in self.passages]
            vecs = embed(texts)
            self._vectors = {p.id: v for p, v in zip(self.passages, vecs)}

    def retrieve(self, query: str, embed, k: int = 3) -> list[Passage]:
        """Top-k passages by cosine similarity; deterministic tie-break by id."""
        self._ensure_vectors(embed)
        qv = embed([query])[0]
        scored: list[tuple[float, str]] = []
        for pid, vec in self._vectors.items():
            scored.append((_cosine(qv, vec), pid))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [self._by_id[pid] for _, pid in scored[:k]]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
