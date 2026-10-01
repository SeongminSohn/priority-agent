"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Pinned literal so replaying this migration is reproducible even if settings.embedding_dim changes.
EMBEDDING_DIM = 1536

_PRIORITY_VALUES = "('P0', 'P1', 'P2', 'P3')"


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _jsonb_default(name: str, literal: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(),
        server_default=sa.text(f"'{literal}'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "boards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("trello_board_id", sa.String(), nullable=False),
        sa.Column("trello_token_encrypted", sa.Text(), nullable=True),
        sa.Column("webhook_id", sa.String(), nullable=True),
        _created_at(),
        sa.UniqueConstraint("trello_board_id", name="uq_boards_trello_board_id"),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("board_id", sa.Uuid(), sa.ForeignKey("boards.id"), nullable=True),
        sa.Column("trello_card_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        _jsonb_default("labels", "[]"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        _jsonb_default("members", "[]"),
        _jsonb_default("raw", "{}"),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tickets_board_id", "tickets", ["board_id"])
    op.create_index("ix_tickets_trello_card_id", "tickets", ["trello_card_id"], unique=True)
    op.create_index(
        "ix_tickets_embedding_cosine",
        "tickets",
        ["embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_agent_runs_status"
        ),
    )
    op.create_index("ix_agent_runs_ticket_id", "agent_runs", ["ticket_id"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_step"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])

    op.create_table(
        "triage_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("priority", sa.String(2), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("estimated_hours", sa.Float(), nullable=True),
        _jsonb_default("risk_flags", "[]"),
        _created_at(),
        sa.CheckConstraint(f"priority IN {_PRIORITY_VALUES}", name="ck_triage_results_priority"),
    )
    op.create_index("ix_triage_results_run_id", "triage_results", ["run_id"])
    op.create_index("ix_triage_results_ticket_id", "triage_results", ["ticket_id"])

    op.create_table(
        "overrides",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("original_priority", sa.String(2), nullable=False),
        sa.Column("final_priority", sa.String(2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("overridden_by", sa.String(), nullable=True),
        sa.Column(
            "ticket_text_embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True
        ),
        _created_at(),
        sa.CheckConstraint(
            f"original_priority IN {_PRIORITY_VALUES}", name="ck_overrides_original_priority"
        ),
        sa.CheckConstraint(
            f"final_priority IN {_PRIORITY_VALUES}", name="ck_overrides_final_priority"
        ),
    )
    op.create_index("ix_overrides_ticket_id", "overrides", ["ticket_id"])
    op.create_index(
        "ix_overrides_ticket_text_embedding_cosine",
        "overrides",
        ["ticket_text_embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"ticket_text_embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("overrides")
    op.drop_table("triage_results")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("tickets")
    op.drop_table("boards")
