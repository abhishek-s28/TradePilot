"""Options chain endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.data.factory import get_provider
from app.models.domain import OptionRight
from app.options.scanner import OptionsFilter, scan_chain

router = APIRouter(prefix="/options", tags=["options"])


class OptionsScanRequest(BaseModel):
    underlying: str
    min_open_interest: int = 100
    min_volume: int = 10
    max_spread_pct: float = 0.10
    max_price: Optional[float] = None
    min_delta: Optional[float] = None
    max_delta: Optional[float] = None
    right: Optional[OptionRight] = None
    min_dte: int = 1
    max_dte: int = 60
    expiration_from: Optional[datetime] = None
    expiration_to: Optional[datetime] = None


@router.get("/chain/{ticker}")
async def chain(ticker: str) -> list[dict]:
    provider = await get_provider()
    chain = await provider.get_options_chain(ticker)
    return [
        {
            "symbol": c.symbol, "underlying": c.underlying,
            "expiration": c.expiration.isoformat(),
            "strike": c.strike, "right": c.right.value,
            "bid": c.bid, "ask": c.ask, "mid": c.mid,
            "volume": c.volume, "open_interest": c.open_interest,
            "implied_volatility": c.implied_volatility,
            "delta": c.delta, "gamma": c.gamma, "theta": c.theta, "vega": c.vega,
            "spread_pct": round(c.spread_pct, 4),
            "liquidity_score": c.liquidity_score,
        }
        for c in chain
    ]


@router.post("/scan")
async def scan(req: OptionsScanRequest) -> dict:
    f = OptionsFilter(
        expiration_from=req.expiration_from,
        expiration_to=req.expiration_to,
        min_open_interest=req.min_open_interest,
        min_volume=req.min_volume,
        max_spread_pct=req.max_spread_pct,
        max_price=req.max_price,
        min_delta=req.min_delta,
        max_delta=req.max_delta,
        right=req.right,
        min_dte=req.min_dte,
        max_dte=req.max_dte,
    )
    results = await scan_chain(req.underlying, f)
    return {"count": len(results), "results": results}
