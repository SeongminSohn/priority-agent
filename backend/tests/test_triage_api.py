import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.triage import get_llm, get_session, get_trello_client
from app.config import Settings
from app.integrations.trello import (
    TrelloAuthError,
    TrelloCard,
    TrelloNotFoundError,
    TrelloRateLimitError,
)
from app.llm.prompts.triage import TriageOutput
from app.main import app
from app.persistence.models import AgentRun, TriageResult

CARD = TrelloCard.model_validate(
    {
        "id": "abc123",
        "name": "Fix login crash",
        "desc": "users cannot log in",
        "labels": [],
        "idList": "l",
        "idBoard": "b",
        "url": "https://trello.com/c/abc123",
    }
)
GOOD = TriageOutput(priority="P0", category="bug", reasoning="prod down", estimated_hours=3)


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
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
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


def make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = uuid.uuid4()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture
def session() -> Iterator[MagicMock]:
    s = make_session()
    app.dependency_overrides[get_session] = lambda: s
    yield s
    app.dependency_overrides.clear()


def setup(trello: FakeTrello, llm: FakeLLM) -> TestClient:
    app.dependency_overrides[get_trello_client] = lambda: trello
    app.dependency_overrides[get_llm] = lambda: llm
    return TestClient(app)


def added(session: MagicMock, cls: type) -> list[Any]:
    return [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], cls)]


def test_success(session: MagicMock) -> None:
    llm = FakeLLM([GOOD])
    resp = setup(FakeTrello(), llm).post("/triage/abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["priority"] == "P0" and body["category"] == "bug"
    assert body["card_id"] == "abc123" and body["run_id"]
    run = added(session, AgentRun)[0]
    assert run.status == "succeeded" and run.finished_at is not None
    assert added(session, TriageResult)[0].priority == "P0"


def test_validation_failure_then_retry_succeeds(session: MagicMock) -> None:
    llm = FakeLLM([validation_error(), GOOD])
    resp = setup(FakeTrello(), llm).post("/triage/abc123")
    assert resp.status_code == 200
    assert len(llm.structured.calls) == 2
    retry = llm.structured.calls[1]
    assert len(retry) == 3
    assert "Correction" in str(retry[2].content)
    assert "priority" in str(retry[2].content)
    assert "P9" not in str(retry[2].content)


def test_validation_failure_after_retry_returns_502(session: MagicMock) -> None:
    llm = FakeLLM([validation_error(), validation_error(), GOOD])
    resp = setup(FakeTrello(), llm).post("/triage/abc123")
    assert resp.status_code == 502
    assert len(llm.structured.calls) == 2
    assert added(session, AgentRun)[0].status == "failed"
    assert not added(session, TriageResult)


def test_llm_exception_returns_502_and_fails_run(session: MagicMock) -> None:
    llm = FakeLLM([RuntimeError("secret-key-123 boom")])
    resp = setup(FakeTrello(), llm).post("/triage/abc123")
    assert resp.status_code == 502
    assert "secret-key-123" not in resp.text
    run = added(session, AgentRun)[0]
    assert run.status == "failed"
    assert "secret-key-123" not in (run.error or "")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (TrelloNotFoundError("x"), 404),
        (TrelloAuthError("x"), 502),
        (TrelloRateLimitError("x"), 503),
    ],
)
def test_trello_error_mapping(session: MagicMock, error: Exception, status: int) -> None:
    resp = setup(FakeTrello(error), FakeLLM([GOOD])).post("/triage/abc123")
    assert resp.status_code == status
    session.add.assert_not_called()


@pytest.mark.parametrize("card_id", ["bad-id", "a" * 65, "x%20y"])
def test_invalid_card_id_returns_422(session: MagicMock, card_id: str) -> None:
    resp = setup(FakeTrello(), FakeLLM([GOOD])).post(f"/triage/{card_id}")
    assert resp.status_code == 422


def test_unexpected_trello_response_returns_502(session: MagicMock) -> None:
    resp = setup(FakeTrello(ValueError("raw secret body")), FakeLLM([GOOD])).post("/triage/abc123")
    assert resp.status_code == 502
    assert "secret" not in resp.text


def auth_settings(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    base: dict[str, Any] = {
        "app_env": "local",
        "triage_api_key": "k3y",
    }
    base.update(kw)
    monkeypatch.setattr("app.api.triage.get_settings", lambda: Settings(**base))


def test_api_key_missing_or_wrong_returns_401(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_settings(monkeypatch)
    client = setup(FakeTrello(), FakeLLM([GOOD]))
    assert client.post("/triage/abc123").status_code == 401
    resp = client.post("/triage/abc123", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
    assert "k3y" not in resp.text


def test_api_key_correct_passes(session: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    auth_settings(monkeypatch)
    client = setup(FakeTrello(), FakeLLM([GOOD]))
    assert client.post("/triage/abc123", headers={"X-API-Key": "k3y"}).status_code == 200


def test_non_local_without_key_returns_503(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Settings validation would reject this config at startup; bypass it to test the guard itself.
    settings = Settings.model_construct(app_env="prod", triage_api_key=SecretStr(""))
    monkeypatch.setattr("app.api.triage.get_settings", lambda: settings)
    client = setup(FakeTrello(), FakeLLM([GOOD]))
    assert client.post("/triage/abc123", headers={"X-API-Key": "x"}).status_code == 503
