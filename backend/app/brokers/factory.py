"""Broker factory.

The ONE place where:
 - Broker selection happens
 - Live-trading hard-gate is enforced
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter
from app.brokers.paper_broker import PaperBroker
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.paper_trading.engine import PaperEngine

log = get_logger(__name__)

_broker: BrokerAdapter | None = None
_paper_engine: PaperEngine | None = None


def get_paper_engine() -> PaperEngine:
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperEngine()
    return _paper_engine


async def get_broker() -> BrokerAdapter:
    global _broker
    if _broker is not None:
        return _broker

    s = get_settings()

    if s.broker == "alpaca":
        if s.alpaca_trading_paper:
            from app.brokers.alpaca_broker import AlpacaBroker

            _broker = AlpacaBroker(paper=True)
        elif s.can_trade_live:
            from app.brokers.alpaca_broker import AlpacaBroker

            _broker = AlpacaBroker(paper=False)
        else:
            log.warning(
                "broker.alpaca_live_blocked_by_safety",
                live_enabled=s.live_trading_enabled,
                live_unlocked=s.live_trading_unlocked,
                mode=s.trading_mode.value,
                note="Falling back to local paper broker.",
            )
            engine = get_paper_engine()
            await engine.ensure_account()
            _broker = PaperBroker(engine)
    elif s.broker == "ibkr":
        if not s.can_trade_live:
            log.warning(
                "broker.ibkr_blocked_by_safety",
                live_enabled=s.live_trading_enabled,
                live_unlocked=s.live_trading_unlocked,
                mode=s.trading_mode.value,
                note="Falling back to paper broker.",
            )
            engine = get_paper_engine()
            await engine.ensure_account()
            _broker = PaperBroker(engine)
        else:
            from app.brokers.ibkr_broker import IBKRBroker
            _broker = IBKRBroker()
    else:
        engine = get_paper_engine()
        await engine.ensure_account()
        _broker = PaperBroker(engine)

    await _broker.connect()
    try:
        account = await _broker.get_account()
        log.info(
            "broker.selected",
            broker=_broker.name,
            equity=account.equity,
            buying_power=account.buying_power,
            open_positions=account.open_positions,
            today_pnl=account.daily_pnl,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("broker.account_snapshot_failed", broker=_broker.name, error=str(exc))
        log.info("broker.selected", broker=_broker.name)
    return _broker


async def shutdown_broker() -> None:
    global _broker
    if _broker:
        await _broker.disconnect()
        _broker = None


async def reset_broker() -> BrokerAdapter:
    """Drop the cached broker and reconnect through the same safety gates."""
    await shutdown_broker()
    return await get_broker()
