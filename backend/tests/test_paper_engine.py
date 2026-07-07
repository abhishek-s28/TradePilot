"""Paper engine tests.

Covers the paper-trading state machine: account cash, position open/close,
realized P&L tracking, and order recording.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.domain import (
    AssetClass,
    OrderProposal,
    OrderType,
    Side,
    TimeInForce,
)
from app.paper_trading.engine import PaperEngine


def _buy(symbol: str = "AAPL", qty: int = 10, limit: float = 100.0) -> OrderProposal:
    return OrderProposal(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=Side.BUY,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        time_in_force=TimeInForce.DAY,
        estimated_cost=limit * qty,
        estimated_max_loss=limit * qty * 0.02,
        reason="test",
        risk_score=0.1,
    )


def _sell(symbol: str = "AAPL", qty: int = 10) -> OrderProposal:
    return OrderProposal(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=Side.SELL,
        qty=qty,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_cost=0.0,
        estimated_max_loss=0.0,
        reason="test_close",
        risk_score=0.0,
    )


async def test_ensure_account_creates_default(db):
    engine = PaperEngine()
    acct_id = await engine.ensure_account()
    assert acct_id

    snap = await engine.account_snapshot()
    assert snap.cash == 100_000.0
    assert snap.equity == 100_000.0
    assert snap.open_positions == 0


async def test_buy_then_sell_realizes_pnl(db):
    engine = PaperEngine()
    await engine.ensure_account()

    # Buy 10 @ 100 → cash drops by 1000
    await engine.fill_order(
        order_id="o1",
        proposal=_buy(qty=10, limit=100.0),
        fill_price=100.0,
        filled_at=datetime.now(timezone.utc),
    )
    snap = await engine.account_snapshot()
    assert snap.cash == 99_000.0

    positions = await engine.positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "AAPL"
    assert p.qty == 10
    assert p.avg_price == 100.0

    # Sell 10 @ 105 → cash up by 1050, realized P&L +50
    await engine.fill_order(
        order_id="o2",
        proposal=_sell(qty=10),
        fill_price=105.0,
        filled_at=datetime.now(timezone.utc),
    )
    snap = await engine.account_snapshot()
    assert snap.cash == 100_050.0
    # All positions closed
    assert (await engine.positions()) == []


async def test_partial_close_keeps_open_position(db):
    engine = PaperEngine()
    await engine.ensure_account()

    await engine.fill_order("o1", _buy(qty=10, limit=100.0), 100.0, datetime.now(timezone.utc))
    await engine.fill_order("o2", _sell(qty=4), 110.0, datetime.now(timezone.utc))

    positions = await engine.positions()
    assert len(positions) == 1
    assert positions[0].qty == 6


async def test_averaging_into_position(db):
    engine = PaperEngine()
    await engine.ensure_account()

    await engine.fill_order("o1", _buy(qty=10, limit=100.0), 100.0, datetime.now(timezone.utc))
    await engine.fill_order("o2", _buy(qty=10, limit=110.0), 110.0, datetime.now(timezone.utc))

    positions = await engine.positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.qty == 20
    assert p.avg_price == pytest.approx(105.0)


async def test_reset_wipes_everything(db):
    engine = PaperEngine()
    await engine.ensure_account()
    await engine.fill_order("o1", _buy(qty=10, limit=100.0), 100.0, datetime.now(timezone.utc))
    assert (await engine.account_snapshot()).cash == 99_000.0

    await engine.reset(starting_cash=50_000.0)
    snap = await engine.account_snapshot()
    assert snap.cash == 50_000.0
    assert snap.open_positions == 0
    assert (await engine.orders()) == []


async def test_pending_limit_does_not_change_cash(db):
    """record_order with PENDING status (e.g. limit didn't cross) should not
    move cash — only fill_order does that."""
    engine = PaperEngine()
    await engine.ensure_account()

    from app.models.domain import OrderStatus
    await engine.record_order(
        order_id="o1",
        proposal=_buy(qty=10, limit=100.0),
        status=OrderStatus.PENDING,
        filled_qty=0,
        avg_fill_price=None,
    )
    snap = await engine.account_snapshot()
    assert snap.cash == 100_000.0
    assert (await engine.positions()) == []
