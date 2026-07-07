"""News feed API — live headlines, sentiment scoring, insider filings, analyst ratings."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.data.factory import get_provider
from app.services.sec_filings import (
    fetch_analyst_ratings,
    fetch_recent_8k,
    fetch_recent_form4,
    insider_signal,
)
from app.utils.news_sentiment import score_news, score_text

log = get_logger(__name__)
router = APIRouter(prefix="/news", tags=["news"])

# Symbols always included in the broad market feed
_MARKET_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "VXX"]


def _sentiment_tag(polarity: float) -> str:
    if polarity > 0.08:
        return "bullish"
    if polarity < -0.08:
        return "bearish"
    return "neutral"


def _enrich_item(item, symbol_filter: str | None = None) -> dict:
    polarity, impact = score_text(f"{item.headline} {item.summary}")
    magnitude = abs(polarity) * impact
    return {
        "id": item.id,
        "headline": item.headline,
        "summary": item.summary,
        "source": item.source,
        "url": item.url,
        "symbols": item.symbols,
        "created_at": item.created_at.isoformat(),
        "sentiment": {
            "polarity": round(polarity, 3),
            "impact": round(impact, 2),
            "magnitude": round(magnitude, 3),
            "tag": _sentiment_tag(polarity),
        },
    }


@router.get("/feed")
async def get_news_feed(
    symbols: Annotated[str | None, Query(description="Comma-separated symbols, e.g. SPY,AAPL")] = None,
    lookback_hours: Annotated[float, Query(ge=0.5, le=72)] = 8.0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    sentiment: Annotated[str | None, Query(description="Filter: bullish | bearish | neutral")] = None,
):
    """Live news feed with per-headline sentiment scores.

    Pass `symbols` to filter to specific tickers. Omit for broad market news.
    Results are sorted newest-first with a sentiment tag on each item.
    """
    provider = await get_provider()
    sym_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    items = await provider.get_news(sym_list, lookback_hours=lookback_hours, limit=limit)

    enriched = [_enrich_item(it) for it in items]

    if sentiment:
        enriched = [e for e in enriched if e["sentiment"]["tag"] == sentiment]

    # Sort: high-impact bullish/bearish first, then by time
    enriched.sort(
        key=lambda e: (-abs(e["sentiment"]["polarity"]) * e["sentiment"]["impact"],
                       e["created_at"]),
        reverse=False,
    )

    # Aggregate score across all items
    agg = score_news(items)

    return {
        "symbols": sym_list,
        "lookback_hours": lookback_hours,
        "total": len(enriched),
        "aggregate": {
            "polarity": agg.polarity,
            "impact": agg.impact,
            "magnitude": agg.magnitude,
            "direction": agg.direction,
            "headline_count": agg.headline_count,
            "top_headline": agg.top_headline,
        },
        "items": enriched,
    }


@router.get("/market")
async def get_market_news(
    lookback_hours: Annotated[float, Query(ge=0.5, le=48)] = 6.0,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
):
    """Broad market news from SPY, QQQ, IWM, VXX — macro & Fed headlines."""
    provider = await get_provider()
    items = await provider.get_news(_MARKET_SYMBOLS, lookback_hours=lookback_hours, limit=limit)
    enriched = [_enrich_item(it) for it in items]
    enriched.sort(key=lambda e: e["created_at"], reverse=True)
    agg = score_news(items)
    return {
        "symbols": _MARKET_SYMBOLS,
        "total": len(enriched),
        "aggregate": {
            "polarity": agg.polarity,
            "impact": agg.impact,
            "magnitude": agg.magnitude,
            "direction": agg.direction,
            "top_headline": agg.top_headline,
        },
        "items": enriched,
    }


@router.get("/insider/{symbol}")
async def get_insider_activity(
    symbol: str,
    lookback_days: Annotated[int, Query(ge=1, le=30)] = 7,
):
    """SEC Form 4 public insider filings for a symbol.

    Company insiders are legally required to disclose purchases/sales within
    2 business days. A cluster of insider buys is a high-conviction signal.
    Source: SEC EDGAR public API — fully legal, free, no key required.
    """
    symbol = symbol.upper()
    signal = await insider_signal(symbol, lookback_days=lookback_days)
    transactions = await fetch_recent_form4(symbol, lookback_days=lookback_days)
    eightks = await fetch_recent_8k(symbol, lookback_days=min(lookback_days, 7))

    return {
        "symbol": symbol,
        "signal": {
            "direction": signal.direction if signal else None,
            "confidence": signal.confidence if signal else None,
            "reason": signal.reason if signal else None,
            "total_buy_value": signal.total_buy_value if signal else 0,
            "total_sell_value": signal.total_sell_value if signal else 0,
            "transaction_count": signal.transaction_count if signal else 0,
        } if signal else None,
        "transactions": [
            {
                "insider_name": t.insider_name,
                "insider_title": t.insider_title,
                "transaction_type": t.transaction_type,
                "shares": t.shares,
                "price_per_share": t.price_per_share,
                "total_value": t.total_value,
                "transaction_date": t.transaction_date.isoformat(),
                "filing_url": t.filing_url,
            }
            for t in transactions
        ],
        "sec_8k_events": [
            {
                "company_name": e.company_name,
                "filed_at": e.filed_at.isoformat(),
                "items": e.items,
                "description": e.description,
                "filing_url": e.filing_url,
            }
            for e in eightks
        ],
    }


@router.get("/analyst/{symbol}")
async def get_analyst_ratings(
    symbol: str,
    lookback_days: Annotated[int, Query(ge=1, le=14)] = 5,
):
    """Recent analyst upgrade/downgrade/initiation events for a symbol."""
    symbol = symbol.upper()
    ratings = await fetch_analyst_ratings(symbol, lookback_days=lookback_days)
    return {
        "symbol": symbol,
        "total": len(ratings),
        "upgrades": [r for r in ratings if r.action == "upgrade"],
        "downgrades": [r for r in ratings if r.action == "downgrade"],
        "other": [r for r in ratings if r.action not in ("upgrade", "downgrade")],
        "items": [
            {
                "firm": r.firm,
                "action": r.action,
                "from_rating": r.from_rating,
                "to_rating": r.to_rating,
                "price_target": r.price_target,
                "published_at": r.published_at.isoformat(),
                "headline": r.headline,
            }
            for r in ratings
        ],
    }


@router.get("/watchlist")
async def get_watchlist_news(
    lookback_hours: Annotated[float, Query(ge=0.5, le=24)] = 4.0,
):
    """Aggregated news sentiment per symbol for the core watchlist.

    Returns a ranked list: most impactful movers first.
    Useful for the news dashboard overview — shows which symbols have the
    most meaningful news right now.
    """
    from app.auto_trade.watchlist import derived_watchlist
    from app.universe.loader import CORE_OPTIONABLE_SYMBOLS

    provider = await get_provider()
    # Use just the top 30 core symbols to keep latency manageable
    symbols = CORE_OPTIONABLE_SYMBOLS[:30]

    items = await provider.get_news(symbols, lookback_hours=lookback_hours, limit=100)

    # Group by symbol
    by_symbol: dict[str, list] = {}
    for item in items:
        for sym in (item.symbols or []):
            sym = sym.upper()
            if sym in set(symbols):
                by_symbol.setdefault(sym, []).append(item)

    results = []
    for sym, sym_items in by_symbol.items():
        agg = score_news(sym_items, symbol=sym)
        if agg.headline_count == 0:
            continue
        results.append({
            "symbol": sym,
            "headline_count": agg.headline_count,
            "polarity": agg.polarity,
            "impact": agg.impact,
            "magnitude": agg.magnitude,
            "direction": agg.direction,
            "top_headline": agg.top_headline,
        })

    results.sort(key=lambda r: -r["magnitude"])
    return {"lookback_hours": lookback_hours, "symbols_with_news": len(results), "items": results}
