import asyncio
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.integrations import trello
from app.integrations.trello import (
    TrelloAuthError,
    TrelloClient,
    TrelloNetworkError,
    TrelloNotFoundError,
    TrelloRateLimitError,
    TrelloServerError,
)

KEY = "secretkey123"
TOKEN = "secrettoken456"

CARD_JSON = {
    "id": "abc123",
    "name": "Fix bug",
    "desc": "details",
    "labels": [{"id": "l1", "name": "urgent", "color": "red", "idBoard": "b"}],
    "due": "2026-10-05T12:00:00.000Z",
    "idList": "list1",
    "idBoard": "board1",
    "url": "https://trello.com/c/abc123",
    "shortUrl": "https://trello.com/c/abc",
    "dateLastActivity": "2026-10-01T09:00:00.000Z",
    "unknownField": 1,
}


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(trello, "_sleep", fake_sleep)
    return recorded


def make_client(handler, max_retries: int = 3, deadline: float = 20.0) -> TrelloClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=trello.BASE_URL)
    return TrelloClient(
        SecretStr(KEY), SecretStr(TOKEN), client=http, max_retries=max_retries, deadline=deadline
    )


async def test_get_card_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CARD_JSON)

    async with make_client(handler) as client:
        card = await client.get_card("abc123")

    assert card.id_list == "list1"
    assert card.id_board == "board1"
    assert card.labels[0].color == "red"
    assert card.due is not None and card.date_last_activity is not None
    assert seen[0].url.path == "/1/cards/abc123"
    auth = seen[0].headers["Authorization"]
    assert auth == f'OAuth oauth_consumer_key="{KEY}", oauth_token="{TOKEN}"'
    assert KEY not in str(seen[0].url) and TOKEN not in str(seen[0].url)
    assert not seen[0].url.query


async def test_not_found_no_retry(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    with pytest.raises(TrelloNotFoundError) as exc:
        await make_client(handler).get_card("abc123")
    assert calls == 1 and not sleeps
    assert exc.value.retryable is False


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error(status: int) -> None:
    with pytest.raises(TrelloAuthError):
        await make_client(lambda r: httpx.Response(status)).get_card("abc123")


async def test_rate_limit_then_success(sleeps: list[float]) -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json=CARD_JSON),
    ]

    card = await make_client(lambda r: responses.pop(0)).get_card("abc123")
    assert card.id == "abc123"
    assert sleeps == [2.0]


async def test_retry_after_capped(sleeps: list[float]) -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "9999"}),
        httpx.Response(200, json=CARD_JSON),
    ]
    await make_client(lambda r: responses.pop(0), deadline=100.0).get_card("abc123")
    assert sleeps == [trello.MAX_BACKOFF_SECONDS]


async def test_rate_limit_exhausted() -> None:
    with pytest.raises(TrelloRateLimitError) as exc:
        await make_client(lambda r: httpx.Response(429), max_retries=1).get_card("abc123")
    assert exc.value.retryable is True


async def test_server_error_exhausted(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(TrelloServerError):
        await make_client(handler, max_retries=3).get_card("abc123")
    assert calls == 4
    assert sleeps == [0.5, 1.0, 2.0]


async def test_timeout_retried_then_network_error(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    with pytest.raises(TrelloNetworkError):
        await make_client(handler, max_retries=2).get_card("abc123")
    assert len(sleeps) == 2


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a?b=1", "abc 123", "a%2Fb", "a.b"])
async def test_card_id_validation(bad: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    with pytest.raises(ValueError):
        await make_client(handler).get_card(bad)


async def test_errors_and_logs_do_not_leak_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("neterr"):
            raise httpx.ConnectError("failed connecting to " + str(request.url), request=request)
        return httpx.Response(500)

    for card_id in ("abc123", "neterr"):
        with pytest.raises(trello.TrelloError) as exc:
            await make_client(handler, max_retries=1).get_card(card_id)
        assert KEY not in str(exc.value) and TOKEN not in str(exc.value)
        assert exc.value.__cause__ is None
        assert exc.value.__context__ is None or exc.value.__suppress_context__

    assert KEY not in caplog.text and TOKEN not in caplog.text


async def test_retry_after_exceeding_budget_raises_immediately(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "25"})

    with pytest.raises(TrelloRateLimitError):
        await make_client(handler, deadline=5.0).get_card("abc123")
    assert not sleeps


async def test_deadline_exceeded_not_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=CARD_JSON)

    with pytest.raises(TrelloNetworkError) as exc:
        await make_client(handler, deadline=0.05).get_card("abc123")
    assert exc.value.retryable is False
    assert "abc123" in str(exc.value) and KEY not in str(exc.value)


@pytest.mark.parametrize("bad", [-1, 6])
def test_max_retries_range(bad: int) -> None:
    with pytest.raises(ValueError):
        make_client(lambda r: httpx.Response(200), max_retries=bad)


async def test_card_id_length_limit() -> None:
    client = make_client(lambda r: httpx.Response(200, json=CARD_JSON))
    await client.get_card("a" * 64)
    with pytest.raises(ValueError):
        await client.get_card("a" * 65)
