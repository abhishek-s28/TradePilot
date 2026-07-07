"""Risk config loader tests.

These verify the bridge between persisted RiskSettings and the in-memory RiskConfig
the RiskManager consumes. The kill switch is the most important one: if this
bridge is broken, the UI toggle is theatre.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.database.models import RiskSettings
from app.database.session import session_factory
from app.core.settings import TradingMode
from app.models.domain import (
    AccountSnapshot,
    AssetClass,
    Direction,
    Quote,
    Signal,
    SignalStatus,
)
from app.risk.loader import load_risk_config
from app.risk.manager import RiskManager, RiskState


async def test_loader_returns_defaults_when_no_row(db):
    cfg = await load_risk_config()
    assert cfg.max_daily_loss_usd == 1500.0
    assert cfg.max_trade_loss_usd == 500.0
    assert cfg.max_open_positions == 20
    assert cfg.max_trades_per_day == 80
    assert cfg.max_option_premium_usd == 500.0
    assert cfg.kill_switch_active is False


async def test_kill_switch_persists_through_loader(db):
    async with session_factory()() as s:
        s.add(RiskSettings(kill_switch_active=True))
        await s.commit()

    cfg = await load_risk_config()
    assert cfg.kill_switch_active is True


async def test_kill_switch_blocks_evaluation_via_loader(db):
    """Full path: persist kill switch on → load config → RiskManager rejects."""
    async with session_factory()() as s:
        s.add(RiskSettings(kill_switch_active=True))
        await s.commit()

    cfg = await load_risk_config()
    now = datetime.now(timezone.utc)
    sig = Signal(
        strategy="test", asset_class=AssetClass.STOCK, symbol="AAPL",
        direction=Direction.BULLISH, entry=100.0, stop_loss=98.0,
        take_profit=104.0, confidence=0.7, reason="t",
        generated_at=now, status=SignalStatus.NEW,
    )
    q = Quote(symbol="AAPL", bid=99.99, ask=100.01, last=100.0, timestamp=now)
    a = AccountSnapshot(cash=10_000, equity=10_000, buying_power=10_000, positions_value=0)

    decision = RiskManager().evaluate_signal(
        signal=sig, quote=q, account=a, state=RiskState(), config=cfg,
    )
    assert decision.approved is False
    assert "kill_switch_active" in decision.reasons


async def test_allowed_tickers_persist_through_loader(db):
    async with session_factory()() as s:
        s.add(RiskSettings(allowed_tickers=["AAPL", "MSFT"]))
        await s.commit()

    cfg = await load_risk_config()
    assert cfg.allowed_tickers == ["AAPL", "MSFT"]


async def test_non_live_mode_caps_aggressive_persisted_limits(db, monkeypatch):
    from app.core.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "broker", "alpaca")
    monkeypatch.setattr(settings, "alpaca_trading_paper", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "live_trading_unlocked", False)

    async with session_factory()() as s:
        s.add(
            RiskSettings(
                max_daily_loss_usd=1000,
                max_trade_loss_usd=800,
                max_open_positions=20,
                max_trades_per_day=80,
                max_option_premium_usd=800,
            )
        )
        await s.commit()

    cfg = await load_risk_config()

    assert cfg.max_daily_loss_usd == 1000.0
    assert cfg.max_trade_loss_usd == 500.0
    assert cfg.max_open_positions == 20
    assert cfg.max_trades_per_day == 80
    assert cfg.max_option_premium_usd == 500.0
    assert cfg.allow_multiple_option_positions_per_underlying is True
    assert cfg.min_confidence_regular == 0.62


async def test_non_live_mode_scales_caps_to_small_account_equity(db, monkeypatch):
    from app.core.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "broker", "alpaca")
    monkeypatch.setattr(settings, "alpaca_trading_paper", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "live_trading_unlocked", False)

    async with session_factory()() as s:
        s.add(
            RiskSettings(
                max_daily_loss_usd=1500,
                max_trade_loss_usd=500,
                max_open_positions=10,
                max_trades_per_day=30,
                max_option_premium_usd=500,
            )
        )
        await s.commit()

    cfg = await load_risk_config(equity=250)

    assert cfg.max_daily_loss_usd == 250.0
    assert cfg.max_trade_loss_usd == 250.0
    assert cfg.max_option_premium_usd == 250.0
    assert cfg.max_open_positions == 10
    assert cfg.max_trades_per_day == 30


async def test_live_capable_mode_does_not_cap_persisted_limits(db, monkeypatch):
    from app.core.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "broker", "alpaca")
    monkeypatch.setattr(settings, "alpaca_trading_paper", False)
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    monkeypatch.setattr(settings, "live_trading_unlocked", True)
    monkeypatch.setattr(settings, "trading_mode", TradingMode.AUTO)

    async with session_factory()() as s:
        s.add(
            RiskSettings(
                max_daily_loss_usd=40,
                max_trade_loss_usd=15,
                max_open_positions=4,
                max_trades_per_day=12,
                max_option_premium_usd=40,
            )
        )
        await s.commit()

    cfg = await load_risk_config()

    assert cfg.max_daily_loss_usd == 40.0
    assert cfg.max_trade_loss_usd == 15.0
    assert cfg.max_open_positions == 4
    assert cfg.max_trades_per_day == 12
    assert cfg.max_option_premium_usd == 40.0
