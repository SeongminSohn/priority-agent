import asyncio
import logging
import re
from datetime import datetime
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger(__name__)

BASE_URL = "https://api.trello.com/1"
DEFAULT_TIMEOUT = 10.0
DEFAULT_DEADLINE = 20.0
MAX_RETRIES_LIMIT = 5
BACKOFF_BASE_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 30.0
_CARD_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


class TrelloError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, card_id: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.card_id = card_id
        self.status_code = status_code


class TrelloNotFoundError(TrelloError):
    retryable = False


class TrelloAuthError(TrelloError):
    retryable = False


class TrelloRateLimitError(TrelloError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        card_id: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message, card_id=card_id, status_code=status_code)
        self.retry_after = retry_after


class TrelloServerError(TrelloError):
    retryable = True


class TrelloNetworkError(TrelloError):
    retryable = True


class TrelloDeadlineError(TrelloNetworkError):
    retryable = False


class TrelloLabel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str = ""
    color: str | None = None


class TrelloCard(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    desc: str = ""
    labels: list[TrelloLabel] = Field(default_factory=list)
    due: datetime | None = None
    id_list: str = Field(alias="idList")
    id_board: str = Field(alias="idBoard")
    url: str
    date_last_activity: datetime | None = Field(default=None, alias="dateLastActivity")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class TrelloClient:
    def __init__(
        self,
        api_key: SecretStr,
        token: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        deadline: float = DEFAULT_DEADLINE,
    ) -> None:
        if not 0 <= max_retries <= MAX_RETRIES_LIMIT:
            raise ValueError(f"max_retries must be between 0 and {MAX_RETRIES_LIMIT}")
        self._api_key = api_key
        self._token = token
        self._max_retries = max_retries
        self._deadline = deadline
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_card(self, card_id: str) -> TrelloCard:
        if not _CARD_ID_RE.fullmatch(card_id):
            raise ValueError("card_id must be 1-64 alphanumeric characters")

        try:
            async with asyncio.timeout(self._deadline) as scope:
                return await self._get_card_with_retries(card_id, scope)
        except TimeoutError:
            msg = f"Trello request deadline exceeded (card {card_id})"
            raise TrelloDeadlineError(msg, card_id=card_id) from None

    async def _get_card_with_retries(self, card_id: str, scope: asyncio.Timeout) -> TrelloCard:
        attempt = 0
        while True:
            try:
                return await self._fetch_card(card_id)
            except TrelloError as err:
                logger.warning(
                    "trello get_card failed card_id=%s status=%s attempt=%d retryable=%s",
                    card_id,
                    err.status_code,
                    attempt + 1,
                    err.retryable,
                )
                if not err.retryable or attempt >= self._max_retries:
                    raise
                delay = self._delay(err, attempt)
                when = scope.when()
                remaining = when - asyncio.get_running_loop().time() if when else delay
                if delay > remaining:
                    raise
                await _sleep(delay)
                attempt += 1

    @staticmethod
    def _delay(err: TrelloError, attempt: int) -> float:
        if isinstance(err, TrelloRateLimitError) and err.retry_after is not None:
            return min(err.retry_after, MAX_BACKOFF_SECONDS)
        return min(BACKOFF_BASE_SECONDS * 2**attempt, MAX_BACKOFF_SECONDS)

    async def _fetch_card(self, card_id: str) -> TrelloCard:
        base_url = str(self._client.base_url).rstrip("/") or BASE_URL
        # Absolute URL so injected clients without a base_url still work.
        url = f"{base_url}/cards/{card_id}"
        try:
            response = await self._client.get(
                url,
                headers={
                    "Authorization": (
                        f'OAuth oauth_consumer_key="{self._api_key.get_secret_value()}", '
                        f'oauth_token="{self._token.get_secret_value()}"'
                    )
                },
            )
        except httpx.TimeoutException:
            msg = f"Trello request timed out (card {card_id})"
            raise TrelloNetworkError(msg, card_id=card_id) from None
        except httpx.TransportError:
            msg = f"Trello connection failed (card {card_id})"
            raise TrelloNetworkError(msg, card_id=card_id) from None

        status = response.status_code
        if status == 200:
            return TrelloCard.model_validate(response.json())

        msg = f"Trello returned status {status} for card {card_id}"
        kwargs = {"card_id": card_id, "status_code": status}
        if status == 404:
            raise TrelloNotFoundError(msg, **kwargs)
        if status in (401, 403):
            raise TrelloAuthError(msg, **kwargs)
        if status == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise TrelloRateLimitError(msg, retry_after=retry_after, **kwargs)
        if status >= 500:
            raise TrelloServerError(msg, **kwargs)
        raise TrelloError(msg, **kwargs)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
