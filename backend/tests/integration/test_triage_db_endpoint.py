from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.triage import get_llm, get_session, get_trello_client
from app.integrations.trello import TrelloCard, TrelloNotFoundError
from app.llm.prompts.triage import TriageOutput
from app.main import app

CARD = TrelloCard.model_validate(
    {
        "id": "abc123",
        "name": "Fix login crash",
        "desc": "users cannot log in",
        "labels": [{"id": "lb1", "name": "urgent", "color": "red"}],
        "due": "2026-11-01T09:00:00.000Z",
        "idList": "l",
        "idBoard": "b",
        "url": "https://trello.com/c/abc123",
    }
)
GOOD = TriageOutput(
    priority="P0", category="bug", reasoning="prod down", estimated_hours=3, risk_flags=["outage"]
)


class FakeTrello:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get_card(self, card_id: str) -> TrelloCard:
        if self.error:
            raise self.error
        return CARD


class FakeStructured:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def ainvoke(self, messages: Any) -> Any:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeLLM:
    def __init__(self, outcomes: list[Any]) -> None:
        self.structured = FakeStructured(outcomes)

    def with_structured_output(self, schema: type) -> FakeStructured:
        return self.structured


def validation_error() -> Exception:
    try:
        TriageOutput(priority="P9", category="bug", reasoning="x")  # type: ignore[arg-type]
    except Exception as err:
        return err
    raise AssertionError


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def use_fakes(trello: FakeTrello, llm: FakeLLM) -> None:
    app.dependency_overrides[get_trello_client] = lambda: trello
    app.dependency_overrides[get_llm] = lambda: llm


def rows(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)).mappings())


async def test_success_persists_rows(client: httpx.AsyncClient, sync_engine: Engine) -> None:
    use_fakes(FakeTrello(), FakeLLM([GOOD]))
    resp = await client.post("/triage/abc123")
    assert resp.status_code == 200
    body = resp.json()

    tickets = rows(sync_engine, "SELECT * FROM tickets")
    runs = rows(sync_engine, "SELECT * FROM agent_runs")
    results = rows(sync_engine, "SELECT * FROM triage_results")
    assert len(tickets) == len(runs) == len(results) == 1

    ticket, run, result = tickets[0], runs[0], results[0]
    assert ticket["trello_card_id"] == "abc123"
    assert ticket["title"] == "Fix login crash"
    assert ticket["description"] == "users cannot log in"
    assert ticket["labels"][0]["name"] == "urgent"
    assert ticket["due_date"].isoformat().startswith("2026-11-01T09:00:00")
    assert ticket["raw"]["id"] == "abc123"
    assert ticket["members"] == []

    assert run["status"] == "succeeded"
    assert run["ticket_id"] == ticket["id"]
    assert run["finished_at"] is not None and run["error"] is None

    assert result["run_id"] == run["id"] and result["ticket_id"] == ticket["id"]
    assert (result["priority"], result["category"], result["reasoning"]) == (
        "P0",
        "bug",
        "prod down",
    )
    assert result["estimated_hours"] == 3.0
    assert result["risk_flags"] == ["outage"]

    assert body["run_id"] == str(run["id"])
    assert body["card_id"] == "abc123"
    assert body["priority"] == result["priority"]
    assert body["risk_flags"] == ["outage"]


async def test_repeat_call_upserts_ticket(client: httpx.AsyncClient, sync_engine: Engine) -> None:
    use_fakes(FakeTrello(), FakeLLM([GOOD]))
    first = await client.post("/triage/abc123")
    use_fakes(FakeTrello(), FakeLLM([GOOD]))
    second = await client.post("/triage/abc123")
    assert first.status_code == second.status_code == 200
    assert first.json()["run_id"] != second.json()["run_id"]

    assert len(rows(sync_engine, "SELECT 1 FROM tickets")) == 1
    assert len(rows(sync_engine, "SELECT 1 FROM agent_runs")) == 2
    assert len(rows(sync_engine, "SELECT 1 FROM triage_results")) == 2


async def test_validation_failure_then_retry_succeeds(
    client: httpx.AsyncClient, sync_engine: Engine
) -> None:
    llm = FakeLLM([validation_error(), GOOD])
    use_fakes(FakeTrello(), llm)
    resp = await client.post("/triage/abc123")
    assert resp.status_code == 200
    assert llm.structured.calls == 2
    assert [r["status"] for r in rows(sync_engine, "SELECT status FROM agent_runs")] == [
        "succeeded"
    ]
    assert len(rows(sync_engine, "SELECT 1 FROM triage_results")) == 1


async def test_llm_final_failure_marks_run_failed(
    client: httpx.AsyncClient, sync_engine: Engine
) -> None:
    use_fakes(FakeTrello(), FakeLLM([validation_error(), validation_error()]))
    resp = await client.post("/triage/abc123")
    assert resp.status_code == 502

    runs = rows(sync_engine, "SELECT * FROM agent_runs")
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["finished_at"] is not None and runs[0]["error"]
    assert rows(sync_engine, "SELECT 1 FROM triage_results") == []
    # the ticket and the failed run were committed before the LLM call
    assert len(rows(sync_engine, "SELECT 1 FROM tickets")) == 1


async def test_trello_404_writes_nothing(client: httpx.AsyncClient, sync_engine: Engine) -> None:
    use_fakes(FakeTrello(TrelloNotFoundError("nope")), FakeLLM([GOOD]))
    resp = await client.post("/triage/abc123")
    assert resp.status_code == 404
    assert rows(sync_engine, "SELECT 1 FROM agent_runs") == []
    assert rows(sync_engine, "SELECT 1 FROM tickets") == []


def test_priority_check_constraint_rejects_invalid_value(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tickets (id, trello_card_id, title) "
                "VALUES (gen_random_uuid(), 'c1', 't')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, ticket_id, status) "
                "SELECT gen_random_uuid(), id, 'running' FROM tickets"
            )
        )

    def insert(priority: str) -> None:
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO triage_results "
                    "(id, run_id, ticket_id, priority, category, reasoning) "
                    "SELECT gen_random_uuid(), r.id, r.ticket_id, :p, 'bug', 'x' FROM agent_runs r"
                ),
                {"p": priority},
            )

    insert("P3")
    with pytest.raises(IntegrityError, match="ck_triage_results_priority"):
        insert("P9")
    assert len(rows(sync_engine, "SELECT 1 FROM triage_results")) == 1


async def test_concurrent_first_requests_same_card(
    client: httpx.AsyncClient, sync_engine: Engine
) -> None:
    import asyncio

    use_fakes(FakeTrello(), FakeLLM([GOOD, GOOD]))
    responses = await asyncio.gather(client.post("/triage/abc123"), client.post("/triage/abc123"))
    assert [r.status_code for r in responses] == [200, 200]
    assert len(rows(sync_engine, "SELECT 1 FROM tickets")) == 1
    assert len(rows(sync_engine, "SELECT 1 FROM agent_runs")) == 2
