from __future__ import annotations

from app.auto_trade.watchlist import SAFE_AUTOTRADE_SYMBOLS, derived_watchlist


async def test_derived_watchlist_respects_symbol_cap():
    watchlist = await derived_watchlist(max_symbols=5)

    assert watchlist == SAFE_AUTOTRADE_SYMBOLS[:5]
    assert len(watchlist) == 5
