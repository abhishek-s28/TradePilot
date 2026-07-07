"""Yahoo Finance market data provider.

Free, zero-key-required. Covers NYSE, NASDAQ, TSX (.TO), LSE (.L),
Frankfurt (.DE), HKEX (.HK), ASX (.AX), and 60+ other exchanges.

yfinance is synchronous; all calls are dispatched to a thread pool via
asyncio.to_thread so they never block the event loop.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.data.base import MarketDataProvider
from app.models.domain import Bar, OptionContract, OptionRight, Quote

log = get_logger(__name__)

_YF_INTERVAL = {
    "1Min": "1m",
    "5Min": "5m",
    "15Min": "15m",
    "1H": "1h",
    "1Day": "1d",
}


class YahooFinanceProvider(MarketDataProvider):
    """Live market data from Yahoo Finance — no API key required."""

    name = "yahoo"

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        log.info("yahoo.connected")

    async def disconnect(self) -> None:
        self._connected = False
        log.info("yahoo.disconnected")

    # ── market status ─────────────────────────────────────────────────────────

    async def is_market_open(self) -> bool:
        try:
            import yfinance as yf

            def _fetch() -> str:
                return yf.Ticker("SPY").info.get("marketState", "CLOSED")

            state = await asyncio.to_thread(_fetch)
            return state in ("REGULAR", "PRE", "POST")
        except Exception as exc:
            log.warning("yahoo.market_status_failed", error=str(exc))
            now = datetime.now(timezone.utc)
            return now.weekday() < 5

    # ── quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        import yfinance as yf

        def _fetch() -> dict[str, Any]:
            t = yf.Ticker(symbol)
            fi = t.fast_info
            price = float(fi.last_price or fi.regular_market_previous_close or 0)
            # fast_info lacks bid/ask; derive a tight synthetic spread
            spread = max(price * 0.0005, 0.01)
            return {
                "bid": round(price - spread / 2, 4),
                "ask": round(price + spread / 2, 4),
                "last": round(price, 4),
            }

        data = await asyncio.to_thread(_fetch)
        return Quote(
            symbol=symbol,
            bid=data["bid"],
            ask=data["ask"],
            last=data["last"],
            bid_size=100,
            ask_size=100,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batch download via yfinance for efficiency."""
        import yfinance as yf

        if len(symbols) == 1:
            q = await self.get_quote(symbols[0])
            return {symbols[0]: q}

        def _fetch_batch() -> dict[str, float]:
            tickers = yf.Tickers(" ".join(symbols))
            prices: dict[str, float] = {}
            for sym in symbols:
                try:
                    fi = tickers.tickers[sym].fast_info
                    prices[sym] = float(
                        fi.last_price or fi.regular_market_previous_close or 0
                    )
                except Exception:
                    prices[sym] = 0.0
            return prices

        try:
            prices = await asyncio.to_thread(_fetch_batch)
        except Exception as exc:
            log.warning("yahoo.batch_quote_failed", error=str(exc))
            # Graceful fallback to sequential
            results = await asyncio.gather(
                *[self.get_quote(s) for s in symbols], return_exceptions=True
            )
            return {s: r for s, r in zip(symbols, results) if isinstance(r, Quote)}

        now = datetime.now(timezone.utc)
        out: dict[str, Quote] = {}
        for sym, price in prices.items():
            if price > 0:
                spread = max(price * 0.0005, 0.01)
                out[sym] = Quote(
                    symbol=sym,
                    bid=round(price - spread / 2, 4),
                    ask=round(price + spread / 2, 4),
                    last=round(price, 4),
                    bid_size=100,
                    ask_size=100,
                    timestamp=now,
                )
        return out

    # ── OHLCV bars ────────────────────────────────────────────────────────────

    async def get_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]:
        import yfinance as yf

        interval = _YF_INTERVAL.get(timeframe, "5m")

        def _fetch() -> list[Bar]:
            df = yf.Ticker(symbol).history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
            )
            bars: list[Bar] = []
            for ts, row in df.iterrows():
                ts_dt = ts.to_pydatetime()
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                bars.append(
                    Bar(
                        symbol=symbol,
                        timestamp=ts_dt,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(row["Volume"]),
                        vwap=round(
                            (float(row["High"]) + float(row["Low"]) + float(row["Close"])) / 3,
                            4,
                        ),
                    )
                )
            return bars

        return await asyncio.to_thread(_fetch)

    # ── options ───────────────────────────────────────────────────────────────

    async def get_options_chain(
        self, underlying: str, expiration: datetime | None = None
    ) -> list[OptionContract]:
        import yfinance as yf

        def _fetch() -> list[OptionContract]:
            t = yf.Ticker(underlying)
            exps = t.options
            if not exps:
                return []

            if expiration:
                target = min(
                    exps,
                    key=lambda e: abs(
                        (datetime.strptime(e, "%Y-%m-%d") - expiration.replace(tzinfo=None)).days
                    ),
                )
            else:
                target = exps[0]

            chain = t.option_chain(target)
            out: list[OptionContract] = []
            for _, row in chain.calls.iterrows():
                c = _row_to_contract(row, underlying, target, OptionRight.CALL)
                if c:
                    out.append(c)
            for _, row in chain.puts.iterrows():
                c = _row_to_contract(row, underlying, target, OptionRight.PUT)
                if c:
                    out.append(c)
            return out

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            log.error("yahoo.options_chain_failed", symbol=underlying, error=str(exc))
            return []

    async def get_option_quote(self, occ_symbol: str) -> OptionContract:
        from app.data.mock_provider import MockMarketDataProvider

        underlying, exp, right, strike = MockMarketDataProvider._parse_occ(occ_symbol)
        chain = await self.get_options_chain(underlying, exp)
        for c in chain:
            if abs(c.strike - strike) < 0.01 and c.right == right:
                return c
        return OptionContract(
            symbol=occ_symbol,
            underlying=underlying,
            expiration=exp,
            strike=strike,
            right=right,
        )


# ── helpers ───────────────────────────────────────────────────────────────────

def _row_to_contract(
    row: Any, underlying: str, exp_str: str, right: OptionRight
) -> OptionContract | None:
    try:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        bid = float(row.get("bid", 0) or 0)
        ask = float(row.get("ask", 0) or 0)
        strike = float(row.get("strike", 0) or 0)
        if strike == 0:
            return None

        date_str = exp_dt.strftime("%y%m%d")
        r = "C" if right == OptionRight.CALL else "P"
        strike_int = int(round(strike * 1000))
        occ = f"{underlying}{date_str}{r}{strike_int:08d}"

        iv = row.get("impliedVolatility")
        return OptionContract(
            symbol=occ,
            underlying=underlying,
            expiration=exp_dt,
            strike=strike,
            right=right,
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2 if bid or ask else float(row.get("lastPrice", 0) or 0),
            volume=int(row.get("volume", 0) or 0),
            open_interest=int(row.get("openInterest", 0) or 0),
            implied_volatility=float(iv) if iv else None,
        )
    except Exception:
        return None
