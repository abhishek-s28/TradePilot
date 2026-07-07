"""Broker factory tests.

The factory is the one place where the live-trading gate is enforced. These
tests verify it falls back to paper safely under every combination of unsafe
settings.
"""
from __future__ import annotations

import pytest

from app.core.settings import TradingMode


@pytest.fixture
def patch_settings(monkeypatch):
    """Helper: temporarily override a Settings field for the duration of a test."""
    def _patch(**overrides):
        from app.core.settings import get_settings
        s = get_settings()
        for k, v in overrides.items():
            monkeypatch.setattr(s, k, v)
    return _patch


async def test_default_returns_paper(db, patch_settings):
    patch_settings(broker="paper")
    from app.brokers.factory import get_broker
    broker = await get_broker()
    assert broker.name == "paper"
    assert broker.supports_live is False


async def test_ibkr_without_unlock_falls_back_to_paper(db, patch_settings):
    """Asking for IBKR while the live gate is closed must NOT instantiate IBKR."""
    patch_settings(
        broker="ibkr",
        live_trading_enabled=True,
        live_trading_unlocked=False,    # one of the four gates is closed
        trading_mode=TradingMode.SEMI_AUTO,
    )
    from app.brokers.factory import get_broker
    broker = await get_broker()
    assert broker.name == "paper", "Should fall back to paper when unlocked=False"


async def test_ibkr_in_research_mode_falls_back_to_paper(db, patch_settings):
    patch_settings(
        broker="ibkr",
        live_trading_enabled=True,
        live_trading_unlocked=True,
        trading_mode=TradingMode.RESEARCH,  # mode disallows live
    )
    from app.brokers.factory import get_broker
    broker = await get_broker()
    assert broker.name == "paper"


async def test_ibkr_with_enabled_false_falls_back_to_paper(db, patch_settings):
    patch_settings(
        broker="ibkr",
        live_trading_enabled=False,    # env flag explicitly off
        live_trading_unlocked=True,
        trading_mode=TradingMode.SEMI_AUTO,
    )
    from app.brokers.factory import get_broker
    broker = await get_broker()
    assert broker.name == "paper"


async def test_alpaca_paper_broker_allowed_without_live_gates(db, patch_settings, monkeypatch):
    """Alpaca paper routing is allowed without opening real-money live gates."""
    from pydantic import SecretStr

    from app.brokers.alpaca_broker import AlpacaBroker

    async def fake_connect(self):
        self._connected = True

    monkeypatch.setattr(AlpacaBroker, "connect", fake_connect)
    patch_settings(
        broker="alpaca",
        alpaca_trading_paper=True,
        alpaca_api_key=SecretStr("paper-key"),
        alpaca_api_secret=SecretStr("paper-secret"),
        live_trading_enabled=False,
        live_trading_unlocked=False,
        trading_mode=TradingMode.PAPER,
    )

    from app.brokers.factory import get_broker

    broker = await get_broker()
    assert broker.name == "alpaca_paper"
    assert broker.supports_live is False
    assert await broker.is_connected()


async def test_alpaca_live_without_gates_falls_back_to_paper(db, patch_settings):
    patch_settings(
        broker="alpaca",
        alpaca_trading_paper=False,
        live_trading_enabled=False,
        live_trading_unlocked=False,
        trading_mode=TradingMode.PAPER,
    )
    from app.brokers.factory import get_broker

    broker = await get_broker()
    assert broker.name == "paper"


async def test_paper_broker_round_trips_account(db):
    """End-to-end: factory returns a connected paper broker we can query."""
    from app.brokers.factory import get_broker
    broker = await get_broker()
    assert await broker.is_connected()
    account = await broker.get_account()
    assert account.cash == 100_000.0
    assert account.equity == 100_000.0


async def test_can_trade_live_property():
    """The single source of truth for live trading authorisation."""
    from app.core.settings import Settings, TradingMode

    # Default settings: every gate closed → False
    s = Settings()
    assert s.can_trade_live is False

    # All four gates open → True
    s = Settings(
        live_trading_enabled=True,
        live_trading_unlocked=True,
        trading_mode=TradingMode.SEMI_AUTO,
        broker="ibkr",
    )
    assert s.can_trade_live is True

    # Any one gate closed → False
    for kwargs in (
        {"live_trading_enabled": False},
        {"live_trading_unlocked": False},
        {"trading_mode": TradingMode.PAPER},
        {"broker": "paper"},
    ):
        base = {
            "live_trading_enabled": True,
            "live_trading_unlocked": True,
            "trading_mode": TradingMode.SEMI_AUTO,
            "broker": "ibkr",
        }
        base.update(kwargs)
        s = Settings(**base)
        assert s.can_trade_live is False, f"Gate {kwargs} should have closed live trading"

    s = Settings(
        live_trading_enabled=True,
        live_trading_unlocked=True,
        trading_mode=TradingMode.AUTO,
        broker="alpaca",
        alpaca_trading_paper=True,
    )
    assert s.can_trade_live is False

    s = Settings(
        live_trading_enabled=True,
        live_trading_unlocked=True,
        trading_mode=TradingMode.AUTO,
        broker="alpaca",
        alpaca_trading_paper=False,
    )
    assert s.can_trade_live is True
