"""Test fixtures.

Tests run against a real Postgres, created once per session and migrated with Alembic —
the same path production takes. Each test then runs inside a transaction that is rolled
back, so tests share the schema but never each other's data.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from lgapp.config import get_settings
from lgapp.db import get_session
from lgapp.main import create_app

ALEMBIC_INI = "alembic.ini"


def _url_with_database(url: str, database: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{database}"


def alembic_config(url: str) -> Config:
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    return config


async def run_alembic(fn: Any, *args: Any) -> None:
    """Run an Alembic command off the event loop.

    migrations/env.py calls asyncio.run(), which raises if a loop is already running —
    which it always is under pytest-asyncio. A worker thread gives it a clean slate.
    """
    await asyncio.to_thread(fn, *args)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """A throwaway database, so a test run never touches the dev database."""
    return _url_with_database(str(get_settings().database_url), "lgapp_test")


@pytest.fixture(scope="session")
async def _migrated_database(test_database_url: str) -> AsyncIterator[str]:
    admin_url = _url_with_database(test_database_url, "postgres")
    # CREATE DATABASE cannot run inside a transaction.
    admin = _create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql("DROP DATABASE IF EXISTS lgapp_test WITH (FORCE)")
        await conn.exec_driver_sql("CREATE DATABASE lgapp_test")
    await admin.dispose()

    await run_alembic(command.upgrade, alembic_config(test_database_url), "head")

    yield test_database_url

    admin = _create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql("DROP DATABASE IF EXISTS lgapp_test WITH (FORCE)")
    await admin.dispose()


@pytest.fixture
async def connection(_migrated_database: str) -> AsyncIterator[AsyncConnection]:
    """An open transaction, rolled back after the test. Nothing here is ever committed."""
    engine = _create_async_engine(_migrated_database)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session bound to the rolled-back transaction.

    join_transaction_mode="create_savepoint" lets application code call commit() without
    escaping the outer transaction — the test still discards everything.
    """
    maker = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with maker() as s:
        yield s


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose requests share the test's transaction."""
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()
