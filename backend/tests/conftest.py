import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import get_settings

TEST_DB_NAME = "priority_agent_test"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires a real Postgres (pgvector) instance")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.integration)


def _base_url() -> URL:
    return make_url(get_settings().database_url)


@pytest.fixture(scope="session")
def test_db_url() -> Iterator[URL]:
    base = _base_url()
    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    except OperationalError as err:
        admin.dispose()
        pytest.skip(f"Postgres not reachable, skipping integration tests: {type(err).__name__}")

    url = base.set(database=TEST_DB_NAME)
    # alembic/env.py reads the URL from (cached) settings, so point settings at the test DB.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def sync_engine(test_db_url: URL) -> Iterator[Engine]:
    engine = create_engine(test_db_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE triage_results, agent_steps, overrides, agent_runs, tickets, boards "
                "CASCADE"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
async def async_engine(test_db_url: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_db_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(
    async_engine: AsyncEngine, sync_engine: Engine
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest.fixture
def unique_card_id() -> str:
    return uuid.uuid4().hex[:24]
