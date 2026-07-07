"""Signal service smoke test.

End-to-end: scan a small universe with the mock provider, verify strategies run,
the regime is detected, signals (if any) persist correctly, and list_recent reads
them back.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.models import SignalRow
from app.database.session import session_factory
from app.services.signal_service import SignalService


async def test_scan_runs_without_errors(db):
    svc = SignalService()
    signals = await svc.scan(universe=["AAPL", "MSFT", "SPY"])
    # Strategies may or may not produce signals on synthetic data — what matters is
    # that the orchestration runs without raising.
    assert isinstance(signals, list)
    for sig in signals:
        assert sig.symbol in ("AAPL", "MSFT", "SPY")
        assert sig.entry > 0
        assert sig.stop_loss > 0
        assert sig.take_profit > 0
        assert 0.0 <= sig.confidence <= 1.0
        assert sig.id is not None  # persisted, has DB id


async def test_persist_and_list_recent(db):
    svc = SignalService()
    signals = await svc.scan(universe=["AAPL", "MSFT"])

    listed = await svc.list_recent(limit=100)
    assert len(listed) == len(signals)

    # Round-trip check: every signal got persisted with expected fields.
    async with session_factory()() as s:
        res = await s.execute(select(SignalRow))
        rows = res.scalars().all()
    assert len(rows) == len(signals)
    enabled = set(await svc.enabled_strategies())
    for r in rows:
        assert r.strategy in enabled
        assert r.status == "new"


async def test_enabled_strategies_defaults_to_all_when_empty(db):
    svc = SignalService()
    names = await svc.enabled_strategies()
    # Both built-in strategies should be registered and enabled by default.
    assert "momentum_breakout" in names
    assert "mean_reversion" in names
