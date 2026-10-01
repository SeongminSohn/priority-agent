from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    text: str
    k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, str] = Field(default_factory=dict)


class RetrievalDocument(BaseModel):
    doc_id: str
    text: str
    metadata: dict[str, str] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    doc_id: str
    text: str
    score: float
    metadata: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class RetrievalService(Protocol):
    """The only search interface the rest of the app may depend on.

    Implementations (embedding, hybrid, override-based) are swapped via config.
    """

    async def index(self, documents: Sequence[RetrievalDocument]) -> None:
        """Upsert documents keyed by doc_id: an existing doc_id is replaced, not duplicated."""
        ...

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalResult]:
        """Return at most query.k results ordered by score descending.

        - score: higher means more similar; the scale is implementation-specific
          but the direction is fixed.
        - No match yields an empty list, never an error.
        - filters: exact-match on metadata keys. A filter key the implementation
          does not support raises ValueError (filters are never silently ignored).
        """
        ...
