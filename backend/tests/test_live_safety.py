from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.settings import TradingMode


@pytest.fixture
def patch_settings(monkeypatch):
    def _patch(**overrides):
        from app.core.settings import get_settings

        settings = get_settings()
        for key, value in overrides.items():
            monkeypatch.setattr(settings, key, value)

    return _patch


async def test_auto_loop_skips_when_db_auto_trading_disabled(db, patch_settings):
    patch_settings(trading_mode=TradingMode.AUTO)

    from app.auto_trade.loop import AutoTradeLoop

    result = await AutoTradeLoop().scan_and_trade()
    assert result == {"skipped": True, "reason": "auto_trading_disabled_or_kill_switch"}


async def test_live_boot_disables_existing_auto_trading_without_clearing_kill_switch(
    db,
    patch_settings,
):
    patch_settings(
        broker="alpaca",
        alpaca_trading_paper=False,
        live_trading_enabled=True,
        live_trading_unlocked=True,
        trading_mode=TradingMode.AUTO,
    )

    from app.database.models import RiskSettings
    from app.database.session import session_factory
    from app.main import _bootstrap_settings

    async with session_factory()() as session:
        session.add(RiskSettings(auto_trading_enabled=True, kill_switch_active=True))
        await session.commit()

    await _bootstrap_settings()

    async with session_factory()() as session:
        res = await session.execute(select(RiskSettings).limit(1))
        row = res.scalar_one()

    assert row.auto_trading_enabled is False
    assert row.kill_switch_active is True


async def test_risk_halt_persists_kill_switch_and_disables_auto(db):
    from app.auto_trade.loop import AutoTradeLoop
    from app.database.models import RiskSettings, SystemEvent
    from app.database.session import session_factory

    async with session_factory()() as session:
        session.add(RiskSettings(auto_trading_enabled=True, kill_switch_active=False))
        await session.commit()

    await AutoTradeLoop()._persist_risk_halt(-101.0, "daily_loss_limit_hit")

    async with session_factory()() as session:
        row = (await session.execute(select(RiskSettings).limit(1))).scalar_one()
        events = (
            await session.execute(
                select(SystemEvent).where(SystemEvent.kind == "risk_halt_persisted")
            )
        ).scalars().all()

    assert row.auto_trading_enabled is False
    assert row.kill_switch_active is True
    assert len(events) == 1
