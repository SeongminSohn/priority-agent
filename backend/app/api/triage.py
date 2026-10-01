import asyncio
import hmac
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.trello import (
    TrelloAuthError,
    TrelloCard,
    TrelloClient,
    TrelloError,
    TrelloNotFoundError,
    TrelloRateLimitError,
)
from app.llm.factory import get_chat_model
from app.llm.prompts.triage import TriageOutput, build_triage_messages
from app.persistence.db import get_session
from app.persistence.models import AgentRun, Priority, Ticket, TriageResult

logger = logging.getLogger(__name__)

MAX_CONCURRENT_LLM_CALLS = 2

_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)
    return _llm_semaphore


async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    settings = get_settings()
    expected = settings.triage_api_key.get_secret_value()
    if not expected:
        if settings.app_env == "local":
            return
        logger.error("TRIAGE_API_KEY is not configured")
        raise HTTPException(status_code=503, detail="Service not configured")
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


router = APIRouter(prefix="/triage", tags=["triage"], dependencies=[Depends(require_api_key)])

CardId = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{1,64}$")]


class TriageResponse(BaseModel):
    card_id: str
    run_id: uuid.UUID
    priority: Priority
    category: str
    reasoning: str
    estimated_hours: float | None
    risk_flags: list[str]
    model: str


async def get_trello_client() -> AsyncGenerator[TrelloClient, None]:
    settings = get_settings()
    client = TrelloClient(settings.trello_api_key, settings.trello_token)
    try:
        yield client
    finally:
        await client.aclose()


def get_llm() -> BaseChatModel:
    return get_chat_model()


def _trello_http_error(err: TrelloError) -> HTTPException:
    if isinstance(err, TrelloNotFoundError):
        return HTTPException(status_code=404, detail="Trello card not found")
    if isinstance(err, TrelloRateLimitError):
        return HTTPException(status_code=503, detail="Trello rate limit exceeded, retry later")
    if isinstance(err, TrelloAuthError):
        return HTTPException(status_code=502, detail="Trello authentication failed")
    return HTTPException(status_code=502, detail="Failed to fetch card from Trello")


async def _upsert_ticket(session: AsyncSession, card: TrelloCard) -> uuid.UUID:
    values = {
        "trello_card_id": card.id,
        "title": card.name,
        "description": card.desc,
        "labels": [label.model_dump(mode="json") for label in card.labels],
        "due_date": card.due,
        "raw": card.model_dump(mode="json"),
    }
    stmt = pg_insert(Ticket).values(id=uuid.uuid4(), members=[], **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Ticket.trello_card_id], set_={**values, "updated_at": func.now()}
    ).returning(Ticket.id)
    return (await session.execute(stmt)).scalar_one()


def _summarize_validation_error(err: ValidationError) -> str:
    items = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}: {e['msg']}"
        for e in err.errors(include_input=False)[:5]
    ]
    return "; ".join(items)[:500]


async def _classify(llm: BaseChatModel, card: TrelloCard) -> TriageOutput:
    structured = llm.with_structured_output(TriageOutput)
    validation_error: str | None = None
    for attempt in (1, 2):
        try:
            async with _get_llm_semaphore():
                result: Any = await structured.ainvoke(
                    build_triage_messages(card, validation_error)
                )
            if isinstance(result, TriageOutput):
                return result
            return TriageOutput.model_validate(result)
        except (ValidationError, OutputParserException) as err:
            logger.warning("triage output invalid card_id=%s attempt=%d", card.id, attempt)
            if attempt == 2:
                raise
            validation_error = (
                _summarize_validation_error(err)
                if isinstance(err, ValidationError)
                else "output could not be parsed as the required schema"
            )
    raise AssertionError("unreachable")


async def _fail_run(session: AsyncSession, run: AgentRun, message: str) -> None:
    run.status = "failed"
    run.finished_at = datetime.now(UTC)
    run.error = message
    await session.commit()


@router.post("/{card_id}", response_model=TriageResponse)
async def triage_card(
    card_id: CardId,
    trello: Annotated[TrelloClient, Depends(get_trello_client)],
    llm: Annotated[BaseChatModel, Depends(get_llm)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TriageResponse:
    try:
        card = await trello.get_card(card_id)
    except TrelloError as err:
        raise _trello_http_error(err) from err
    except Exception as err:
        logger.error(
            "unexpected trello response card_id=%s error_type=%s", card_id, type(err).__name__
        )
        raise HTTPException(status_code=502, detail="Failed to fetch card from Trello") from None

    ticket_id = await _upsert_ticket(session, card)
    run = AgentRun(
        id=uuid.uuid4(),
        ticket_id=ticket_id,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    try:
        output = await _classify(llm, card)
    except (ValidationError, OutputParserException):
        await _fail_run(session, run, "LLM output failed validation after retry")
        raise HTTPException(status_code=502, detail="LLM returned invalid output") from None
    except Exception as err:
        logger.error("triage llm call failed card_id=%s error_type=%s", card_id, type(err).__name__)
        await _fail_run(session, run, f"LLM call failed: {type(err).__name__}")
        raise HTTPException(status_code=502, detail="LLM call failed") from None

    session.add(
        TriageResult(
            id=uuid.uuid4(),
            run_id=run.id,
            ticket_id=ticket_id,
            priority=output.priority.value,
            category=output.category,
            reasoning=output.reasoning,
            estimated_hours=output.estimated_hours,
            risk_flags=output.risk_flags,
        )
    )
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)
    await session.commit()

    return TriageResponse(
        card_id=card.id,
        run_id=run.id,
        priority=output.priority,
        category=output.category,
        reasoning=output.reasoning,
        estimated_hours=output.estimated_hours,
        risk_flags=output.risk_flags,
        model=get_settings().llm_model,
    )
