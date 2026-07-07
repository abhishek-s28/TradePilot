"""Market data endpoints: bars, quotes, account snapshot."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.data.factory import get_provider
from app.brokers.factory import get_broker

router = APIRouter(tags=["market"])


@router.get("/quote/{symbol}")
async def get_quote(symbol: str) -> dict:
    provider = await get_provider()
    try:
        q = await provider.get_quote(symbol.upper())
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "symbol":    q.symbol,
        "bid":       q.bid,
        "ask":       q.ask,
        "last":      q.last,
        "mid":       q.mid,
        "bid_size":  q.bid_size,
        "ask_size":  q.ask_size,
        "spread":    round(q.spread, 4),
        "spread_pct": round(q.spread_pct, 4),
        "timestamp": q.timestamp.isoformat(),
    }


@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    timeframe: str = Query(default="1d", description="1m 5m 15m 30m 1h 4h 1d 1w"),
    limit: int = Query(default=300, ge=1, le=1000),
) -> list[dict]:
    provider = await get_provider()
    try:
        bars = await provider.get_bars_latest(symbol.upper(), timeframe=timeframe, limit=limit)
    except AttributeError:
        # Fallback for providers that don't implement get_bars_latest
        from datetime import timedelta
        now   = datetime.now(timezone.utc)
        start = now - timedelta(days=max(limit, 365))
        bars  = await provider.get_bars(symbol.upper(), timeframe, start, now)
        bars  = bars[-limit:]
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return [
        {
            "time":   int(b.timestamp.timestamp()),
            "open":   b.open,
            "high":   b.high,
            "low":    b.low,
            "close":  b.close,
            "volume": b.volume,
            "vwap":   b.vwap,
        }
        for b in bars
    ]


@router.get("/account")
async def get_account() -> dict:
    broker = await get_broker()
    acct   = await broker.get_account()
    return acct.model_dump()
