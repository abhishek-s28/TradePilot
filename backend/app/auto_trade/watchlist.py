"""Derived watchlist for the Alpaca paper auto-trader."""
from __future__ import annotations

from app.core.logging import get_logger
from app.universe.loader import (
    CORE_OPTIONABLE_SYMBOLS,
    FALLBACK_SEED,
    filter as universe_filter,
    load_universe,
)

log = get_logger(__name__)

SAFE_AUTOTRADE_SYMBOLS: list[str] = [
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "SMH", "TLT", "GLD",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "JPM",
]


async def universe_count() -> int:
    return len([r for r in await load_universe() if r.tradable])


async def derived_watchlist(max_symbols: int = 30) -> list[str]:
    """Return an optionable watchlist, always pinning the core liquid symbols first.

    Layout: liquid ETFs/mega-caps first, then a small dynamic fill-up. The cap is
    respected even when the core universe is larger than max_symbols.
    """
    safe_core = [s for s in SAFE_AUTOTRADE_SYMBOLS if s in set(CORE_OPTIONABLE_SYMBOLS)]
    pinned: list[str] = list(dict.fromkeys(safe_core))[:max_symbols]

    dynamic: list[str] = []
    try:
        remaining = max(0, max_symbols - len(pinned))
        if remaining > 0:
            records = await universe_filter(
                min_price=5.0,
                min_avg_volume=1_000_000,
                optionable=True,
                limit=remaining + len(pinned),
            )
            dynamic = [
                r.symbol
                for r in records
                if r.tradable and r.optionable and r.symbol not in set(pinned)
            ][:remaining]
    except Exception as exc:  # noqa: BLE001
        log.warning("watchlist.dynamic_filter_failed", error=str(exc))
        fallback_extras = [s for s in FALLBACK_SEED if s not in set(pinned)]
        dynamic = fallback_extras[: max(0, max_symbols - len(pinned))]

    watchlist = pinned + dynamic
    log.info("watchlist.built", pinned=len(pinned), dynamic=len(dynamic), total=len(watchlist))
    return watchlist
