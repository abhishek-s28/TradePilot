"""Portfolio + paper account endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.brokers.factory import get_broker, get_paper_engine

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio")
async def portfolio() -> dict:
    broker = await get_broker()
    account = await broker.get_account()
    positions = await broker.get_positions()
    return {
        "account": account.model_dump(),
        "positions": [p.model_dump() for p in positions],
    }


@router.get("/positions")
async def positions() -> list[dict]:
    broker = await get_broker()
    return [p.model_dump() for p in await broker.get_positions()]


@router.get("/orders")
async def orders() -> list[dict]:
    broker = await get_broker()
    return [
        {
            "id": o.id, "status": o.status.value,
            "filled_qty": o.filled_qty,
            "avg_fill_price": o.avg_fill_price,
            "raw": o.raw,
        }
        for o in await broker.get_orders()
    ]


@router.get("/paper/account")
async def paper_account() -> dict:
    engine = get_paper_engine()
    snap = await engine.account_snapshot()
    return snap.model_dump()


@router.post("/paper/reset")
async def paper_reset(starting_cash: float = 100_000.0) -> dict:
    engine = get_paper_engine()
    await engine.reset(starting_cash=starting_cash)
    return {"status": "reset", "starting_cash": starting_cash}


@router.post("/positions/{symbol}/close")
async def close_position(symbol: str) -> dict:
    """Close an open paper position at current market price."""
    broker = await get_broker()
    order = await broker.close_position(symbol)
    if not order:
        raise HTTPException(404, f"No open position for {symbol}")
    return {
        "status": "closed",
        "symbol": symbol,
        "order_id": order.id,
        "order_status": order.status.value,
        "filled_qty": order.filled_qty,
        "avg_fill_price": order.avg_fill_price,
    }
