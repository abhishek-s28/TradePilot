"""Test configuration. Ensures backend package is importable when pytest is invoked from
either the backend/ dir or the repo root, and sets safe defaults for any module that
reads env at import time."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sure 'app' package resolves regardless of CWD.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Safe defaults for tests so we never accidentally need real services.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATA_PROVIDER", "mock")
os.environ.setdefault("BROKER", "paper")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("LIVE_TRADING_UNLOCKED", "false")
os.environ["ALPACA_API_KEY"] = ""
os.environ["ALPACA_API_SECRET"] = ""
# Use sqlite in-memory async for tests; StaticPool in session.py ensures all
# async sessions in the test see the same database.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402


@pytest.fixture
async def db():
    """Per-test database: create all tables, yield, drop all tables.

    Import inside the fixture so module-level imports in test files that don't
    use the DB (e.g. pure risk-manager tests) don't pay the engine startup cost.
    """
    # Ensure models are registered on Base.metadata
    import app.database.models  # noqa: F401
    from app.database.session import Base, get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level singletons between tests so factories rebuild cleanly."""
    yield
    # Clear data + broker singletons so the next test doesn't inherit state.
    import app.data.factory as data_factory
    import app.brokers.factory as broker_factory

    data_factory._provider = None
    broker_factory._broker = None
    broker_factory._paper_engine = None
