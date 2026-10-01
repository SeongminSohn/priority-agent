import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim

_PRIORITY_VALUES = "('P0', 'P1', 'P2', 'P3')"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    trello_board_id: Mapped[str] = mapped_column(String, unique=True)
    trello_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index(
            "ix_tickets_embedding_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    board_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("boards.id"), nullable=True, index=True
    )
    trello_card_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    labels: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    members: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_agent_runs_status"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_step"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    step_index: Mapped[int]
    node_name: Mapped[str] = mapped_column(String)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)


class TriageResult(Base):
    __tablename__ = "triage_results"
    __table_args__ = (
        CheckConstraint(f"priority IN {_PRIORITY_VALUES}", name="ck_triage_results_priority"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    priority: Mapped[Priority] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String)
    reasoning: Mapped[str] = mapped_column(Text)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_flags: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()


class Override(Base):
    __tablename__ = "overrides"
    __table_args__ = (
        CheckConstraint(
            f"original_priority IN {_PRIORITY_VALUES}", name="ck_overrides_original_priority"
        ),
        CheckConstraint(
            f"final_priority IN {_PRIORITY_VALUES}", name="ck_overrides_final_priority"
        ),
        Index(
            "ix_overrides_ticket_text_embedding_cosine",
            "ticket_text_embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"ticket_text_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    original_priority: Mapped[Priority] = mapped_column(String(2))
    final_priority: Mapped[Priority] = mapped_column(String(2))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_by: Mapped[str | None] = mapped_column(String, nullable=True)
    ticket_text_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
