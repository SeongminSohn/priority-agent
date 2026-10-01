"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Pinned literal so replaying this migration is reproducible even if settings.embedding_dim changes.
EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "cards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("board_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("desc", sa.Text(), server_default="", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_cards_board_id", "cards", ["board_id"])
    op.create_table(
        "triage_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("card_id", sa.String(), sa.ForeignKey("cards.id"), nullable=False),
        sa.Column("priority", sa.String(2), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')", name="ck_triage_results_priority"
        ),
    )
    op.create_index("ix_triage_results_card_id", "triage_results", ["card_id"])


def downgrade() -> None:
    op.drop_index("ix_triage_results_card_id", table_name="triage_results")
    op.drop_table("triage_results")
    op.drop_index("ix_cards_board_id", table_name="cards")
    op.drop_table("cards")
    op.execute("DROP EXTENSION IF EXISTS vector")
