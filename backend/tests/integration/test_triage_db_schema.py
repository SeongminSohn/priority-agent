from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.persistence.models import Base


def test_migrated_schema_matches_models(sync_engine: Engine) -> None:
    with sync_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_pgvector_extension_installed(sync_engine: Engine) -> None:
    with sync_engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar()
