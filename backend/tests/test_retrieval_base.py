from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from app.retrieval.base import (
    RetrievalDocument,
    RetrievalQuery,
    RetrievalResult,
    RetrievalService,
)


class FakeRetriever:
    def __init__(self) -> None:
        self._docs: dict[str, RetrievalDocument] = {}

    async def index(self, documents: Sequence[RetrievalDocument]) -> None:
        for doc in documents:
            self._docs[doc.doc_id] = doc

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalResult]:
        unsupported = set(query.filters) - {"board_id"}
        if unsupported:
            raise ValueError(f"unsupported filters: {sorted(unsupported)}")
        results = [
            RetrievalResult(doc_id=d.doc_id, text=d.text, score=1.0, metadata=d.metadata)
            for d in self._docs.values()
            if query.text in d.text
            and all(d.metadata.get(k) == v for k, v in query.filters.items())
        ]
        return results[: query.k]


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeRetriever(), RetrievalService)


def test_non_conforming_object_rejected() -> None:
    assert not isinstance(object(), RetrievalService)


@pytest.mark.parametrize("k", [0, 51, -1])
def test_query_k_out_of_range(k: int) -> None:
    with pytest.raises(ValidationError):
        RetrievalQuery(text="x", k=k)


def test_query_defaults() -> None:
    q = RetrievalQuery(text="x")
    assert q.k == 5
    assert q.filters == {}


async def test_index_is_upsert_and_empty_result_is_list() -> None:
    r = FakeRetriever()
    await r.index([RetrievalDocument(doc_id="1", text="old")])
    await r.index([RetrievalDocument(doc_id="1", text="new")])
    assert [x.text for x in await r.retrieve(RetrievalQuery(text="new"))] == ["new"]
    assert await r.retrieve(RetrievalQuery(text="zzz")) == []


async def test_unsupported_filter_raises() -> None:
    with pytest.raises(ValueError):
        await FakeRetriever().retrieve(RetrievalQuery(text="x", filters={"bad": "1"}))
