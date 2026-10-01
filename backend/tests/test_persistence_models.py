import sys

from app.config import get_settings
from app.persistence.models import Base, Card, Priority, TriageResult


def test_priority_values() -> None:
    assert [p.value for p in Priority] == ["P0", "P1", "P2", "P3"]
    assert Priority("P0") is Priority.P0


def test_tables_and_columns() -> None:
    assert set(Base.metadata.tables) == {"cards", "triage_results"}
    assert {"id", "board_id", "name", "desc", "embedding", "created_at", "updated_at"} <= set(
        Card.__table__.c.keys()
    )
    assert {"id", "card_id", "priority", "reasoning", "model", "created_at"} <= set(
        TriageResult.__table__.c.keys()
    )


def test_embedding_dim_follows_settings() -> None:
    col = Card.__table__.c.embedding
    assert col.nullable
    assert col.type.dim == get_settings().embedding_dim


def test_triage_result_fk_and_multiple_rows_allowed() -> None:
    col = TriageResult.__table__.c.card_id
    assert {fk.target_fullname for fk in col.foreign_keys} == {"cards.id"}
    assert not col.unique


def test_db_module_import_does_not_connect() -> None:
    sys.modules.pop("app.persistence.db", None)
    import app.persistence.db as db

    assert db.get_engine.cache_info().currsize == 0
