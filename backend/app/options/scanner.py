"""Options chain scanner with liquidity scoring, IV rank, unusual volume detection,
and multi-leg strategy ranking.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.data.factory import get_provider
from app.models.domain import OptionContract, OptionRight

log = get_logger(__name__)


@dataclass
class OptionsFilter:
    expiration_from: datetime | None = None
    expiration_to: datetime | None = None
    min_open_interest: int = 50
    min_volume: int = 5
    max_spread_pct: float = 0.20
    max_price: float | None = None
    min_delta: float | None = None
    max_delta: float | None = None
    right: OptionRight | None = None
    min_dte: int = 0
    max_dte: int = 60
    # IV/volume anomaly filters
    min_iv: float | None = None             # minimum implied volatility (annualised, e.g. 0.30 = 30%)
    min_vol_oi_ratio: float | None = None   # volume / open_interest — unusual activity proxy
    min_liquidity_score: float | None = None


@dataclass
class ScannedContract:
    symbol: str
    underlying: str
    expiration: str
    strike: float
    right: str
    bid: float
    ask: float
    mid: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    spread_pct: float
    liquidity_score: float
    max_loss_per_contract: float
    dte: int
    vol_oi_ratio: float
    is_unusual_volume: bool


async def scan_chain(underlying: str, f: OptionsFilter) -> list[dict]:
    provider = await get_provider()
    now = datetime.now(timezone.utc)
    chain = await provider.get_options_chain(underlying)

    # Compute average volume across the chain for unusual-activity detection
    all_volumes = [c.volume for c in chain if c.volume > 0]
    avg_chain_volume = sum(all_volumes) / len(all_volumes) if all_volumes else 1.0

    out: list[ScannedContract] = []
    for c in chain:
        dte = (c.expiration.date() - now.date()).days
        if dte < f.min_dte or dte > f.max_dte:
            continue
        if f.expiration_from and c.expiration < f.expiration_from:
            continue
        if f.expiration_to and c.expiration > f.expiration_to:
            continue
        if f.right and c.right != f.right:
            continue
        if c.open_interest < f.min_open_interest:
            continue
        if c.volume < f.min_volume:
            continue
        if c.spread_pct > f.max_spread_pct:
            continue
        if f.max_price is not None and c.mid > f.max_price:
            continue
        if c.delta is not None:
            d = abs(c.delta)
            if f.min_delta is not None and d < f.min_delta:
                continue
            if f.max_delta is not None and d > f.max_delta:
                continue
        if f.min_iv is not None and (c.implied_volatility or 0.0) < f.min_iv:
            continue
        if f.min_liquidity_score is not None and c.liquidity_score < f.min_liquidity_score:
            continue

        vol_oi_ratio = (c.volume / c.open_interest) if c.open_interest > 0 else 0.0
        if f.min_vol_oi_ratio is not None and vol_oi_ratio < f.min_vol_oi_ratio:
            continue

        # Unusual volume: contract volume ≥ 3× the chain average and vol/OI > 0.5
        is_unusual = c.volume >= avg_chain_volume * 3 and vol_oi_ratio >= 0.5

        out.append(ScannedContract(
            symbol=c.symbol,
            underlying=c.underlying,
            expiration=c.expiration.isoformat(),
            strike=c.strike,
            right=c.right.value,
            bid=c.bid,
            ask=c.ask,
            mid=c.mid,
            last=c.last,
            volume=c.volume,
            open_interest=c.open_interest,
            implied_volatility=c.implied_volatility,
            delta=c.delta,
            gamma=c.gamma,
            theta=c.theta,
            vega=c.vega,
            spread_pct=round(c.spread_pct, 4),
            liquidity_score=c.liquidity_score,
            max_loss_per_contract=round(c.mid * 100, 2),
            dte=dte,
            vol_oi_ratio=round(vol_oi_ratio, 3),
            is_unusual_volume=is_unusual,
        ))

    # Rank: unusual volume first, then by liquidity, then tightest spread
    out.sort(key=lambda x: (not x.is_unusual_volume, -x.liquidity_score, x.spread_pct))

    log.info(
        "options_scan.done",
        underlying=underlying,
        candidates=len(out),
        chain_size=len(chain),
        unusual_volume=sum(1 for c in out if c.is_unusual_volume),
    )

    return [_to_dict(c) for c in out]


async def scan_unusual_activity(underlying: str, min_dte: int = 0, max_dte: int = 60) -> list[dict]:
    """Return only contracts with unusual options activity (high vol/OI ratio)."""
    return await scan_chain(underlying, OptionsFilter(
        min_dte=min_dte,
        max_dte=max_dte,
        min_open_interest=100,
        min_volume=50,
        min_vol_oi_ratio=0.5,
        max_spread_pct=0.25,
    ))


async def iv_rank_summary(underlying: str) -> dict:
    """Compute a simple IV-rank proxy across the current options chain.

    Returns avg_iv, max_iv, min_iv, and a 0-100 normalised iv_rank_proxy
    useful for strategy selection (high IV → sell premium; low IV → buy premium).
    """
    provider = await get_provider()
    chain = await provider.get_options_chain(underlying)
    ivs = [c.implied_volatility for c in chain if c.implied_volatility and c.implied_volatility > 0]
    if not ivs:
        return {"underlying": underlying, "avg_iv": None, "max_iv": None, "min_iv": None, "iv_rank_proxy": None}

    avg_iv = sum(ivs) / len(ivs)
    max_iv = max(ivs)
    min_iv = min(ivs)
    iv_rank = ((avg_iv - min_iv) / (max_iv - min_iv) * 100) if max_iv > min_iv else 50.0

    return {
        "underlying": underlying,
        "avg_iv": round(avg_iv, 4),
        "max_iv": round(max_iv, 4),
        "min_iv": round(min_iv, 4),
        "iv_rank_proxy": round(iv_rank, 1),
        "sample_size": len(ivs),
    }


def _to_dict(c: ScannedContract) -> dict:
    return {
        "symbol": c.symbol,
        "underlying": c.underlying,
        "expiration": c.expiration,
        "strike": c.strike,
        "right": c.right,
        "bid": c.bid,
        "ask": c.ask,
        "mid": c.mid,
        "last": c.last,
        "volume": c.volume,
        "open_interest": c.open_interest,
        "implied_volatility": c.implied_volatility,
        "delta": c.delta,
        "gamma": c.gamma,
        "theta": c.theta,
        "vega": c.vega,
        "spread_pct": c.spread_pct,
        "liquidity_score": c.liquidity_score,
        "max_loss_per_contract": c.max_loss_per_contract,
        "dte": c.dte,
        "vol_oi_ratio": c.vol_oi_ratio,
        "is_unusual_volume": c.is_unusual_volume,
    }
