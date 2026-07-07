"""Alpaca market data provider.

All alpaca-py SDK calls are synchronous. Every call is wrapped with
asyncio.to_thread so the FastAPI event loop never blocks.

Free IEX feed gives 15-min delayed quotes during market hours and real-time
after close. Upgrade ALPACA_DATA_FEED=sip for full NBBO.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from alpaca.common.exceptions import APIError

from app.core.http import harden_alpaca_client
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.data.base import MarketDataProvider
from app.models.domain import Bar, NewsItem, OptionContract, OptionRight, Quote

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

_TF_MAP = {
    "1m":  ("Minute", 1),
    "5m":  ("Minute", 5),
    "15m": ("Minute", 15),
    "30m": ("Minute", 30),
    "1h":  ("Hour",   1),
    "4h":  ("Hour",   4),
    "1d":  ("Day",    1),
    "1w":  ("Week",   1),
    # legacy keys kept for backwards compat
    "1Min":  ("Minute", 1),
    "5Min":  ("Minute", 5),
    "15Min": ("Minute", 15),
    "1H":    ("Hour",   1),
    "1Day":  ("Day",    1),
}


def _alpaca_timeframe(tf_str: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    unit_str, amount = _TF_MAP.get(tf_str, ("Day", 1))
    unit = {
        "Minute": TimeFrameUnit.Minute,
        "Hour":   TimeFrameUnit.Hour,
        "Day":    TimeFrameUnit.Day,
        "Week":   TimeFrameUnit.Week,
    }[unit_str]
    return TimeFrame(amount, unit)


class AlpacaMarketDataProvider(MarketDataProvider):
    name = "alpaca"

    def __init__(self) -> None:
        s = get_settings()
        s.validate_alpaca_credentials()
        self._key    = s.alpaca_api_key.get_secret_value()
        self._secret = s.alpaca_api_secret.get_secret_value()
        self._feed   = s.alpaca_data_feed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self._stock_data  = StockHistoricalDataClient(self._key, self._secret)
        self._option_data = OptionHistoricalDataClient(self._key, self._secret)
        self._news        = None
        self._trading     = TradingClient(
            self._key,
            self._secret,
            paper=True,
            url_override="https://paper-api.alpaca.markets",
        )
        for client in (self._stock_data, self._option_data, self._trading):
            harden_alpaca_client(client)

    async def connect(self) -> None:
        # Verify creds on startup
        try:
            await asyncio.to_thread(self._trading.get_account)
        except APIError as exc:
            text = str(exc).lower()
            if "403" in text or "401" in text or "forbidden" in text:
                raise RuntimeError(
                    "Alpaca authentication failed for market data/account clock. "
                    "Check ALPACA_API_KEY and ALPACA_API_SECRET."
                ) from exc
            raise
        log.info("alpaca.connected", feed=self._feed)

    async def disconnect(self) -> None:
        log.info("alpaca.disconnected")

    async def is_market_open(self) -> bool:
        clock = await asyncio.to_thread(self._trading.get_clock)
        return bool(clock.is_open)

    async def get_market_clock(self) -> dict:
        clock = await asyncio.to_thread(self._trading.get_clock)
        return {
            "is_open":    bool(clock.is_open),
            "timestamp":  _iso(clock.timestamp),
            "next_open":  _iso(clock.next_open),
            "next_close": _iso(clock.next_close),
        }

    # ── quotes ────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest

        q_req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed)
        t_req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._feed)

        q_resp, t_resp = await asyncio.gather(
            asyncio.to_thread(self._stock_data.get_stock_latest_quote, q_req),
            asyncio.to_thread(self._stock_data.get_stock_latest_trade, t_req),
        )
        q = q_resp[symbol]
        t = t_resp.get(symbol)
        last = float(t.price) if t and t.price else float(q.bid_price or q.ask_price or 0)
        return Quote(
            symbol=symbol,
            bid=float(q.bid_price or 0),
            ask=float(q.ask_price or 0),
            last=last,
            bid_size=int(q.bid_size or 0),
            ask_size=int(q.ask_size or 0),
            timestamp=_ensure_tz(q.timestamp),
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest

        q_req = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=self._feed)
        t_req = StockLatestTradeRequest(symbol_or_symbols=symbols, feed=self._feed)
        q_resp, t_resp = await asyncio.gather(
            asyncio.to_thread(self._stock_data.get_stock_latest_quote, q_req),
            asyncio.to_thread(self._stock_data.get_stock_latest_trade, t_req),
        )
        out: dict[str, Quote] = {}
        for sym, q in q_resp.items():
            t    = t_resp.get(sym)
            last = float(t.price) if t and t.price else float(q.bid_price or q.ask_price or 0)
            out[sym] = Quote(
                symbol=sym,
                bid=float(q.bid_price or 0),
                ask=float(q.ask_price or 0),
                last=last,
                bid_size=int(q.bid_size or 0),
                ask_size=int(q.ask_size or 0),
                timestamp=_ensure_tz(q.timestamp),
            )
        return out

    # ── bars ──────────────────────────────────────────────────────────────

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Bar]:
        from alpaca.data.requests import StockBarsRequest

        tf  = _alpaca_timeframe(timeframe)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=limit,
            feed=self._feed,
        )
        resp = await asyncio.to_thread(self._stock_data.get_stock_bars, req)
        bars: list[Bar] = []
        for b in resp.data.get(symbol, []):
            bars.append(Bar(
                symbol=symbol,
                timestamp=_ensure_tz(b.timestamp),
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=int(b.volume),
                vwap=float(b.vwap) if b.vwap else None,
            ))
        return bars

    async def get_bars_latest(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 300,
    ) -> list[Bar]:
        """Convenience: fetch the last `limit` bars ending now."""
        now   = datetime.now(timezone.utc)
        unit, amount = _TF_MAP.get(timeframe, ("Day", 1))
        # look back far enough to guarantee `limit` bars
        day_mult = {"Minute": 1, "Hour": 1, "Day": 1, "Week": 7}[unit]
        lookback = max(limit * amount * day_mult + 10, 365)
        start = now - timedelta(days=lookback)
        bars  = await self.get_bars(symbol, timeframe, start, now, limit=limit)
        return bars[-limit:]

    # ── options ───────────────────────────────────────────────────────────

    async def get_options_chain(
        self,
        underlying: str,
        expiration: datetime | None = None,
        spot: float = 0.0,
    ) -> list[OptionContract]:
        from alpaca.trading.requests import GetOptionContractsRequest

        expiration_dates = (
            [expiration.date()]
            if expiration
            else _upcoming_weekly_expirations(datetime.now(timezone.utc), count=5)
        )

        contracts = []
        for exp_date in expiration_dates:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                expiration_date=exp_date,
                status="active",
            )
            result = await asyncio.to_thread(self._trading.get_option_contracts, req)
            contracts.extend(result.option_contracts or [])

        symbols = [c.symbol for c in contracts]
        quotes_by_symbol: dict = {}
        if symbols:
            from alpaca.data.requests import OptionLatestQuoteRequest

            async def _fetch_batch(batch):
                qreq = OptionLatestQuoteRequest(symbol_or_symbols=batch)
                try:
                    resp = await asyncio.to_thread(
                        self._option_data.get_option_latest_quote, qreq
                    )
                    return dict(resp) if resp else {}
                except Exception:
                    return {}

            tasks = [_fetch_batch(symbols[i: i + 100]) for i in range(0, len(symbols), 100)]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in raw_results:
                if isinstance(r, dict):
                    quotes_by_symbol.update(r)

        now_utc = datetime.now(timezone.utc)
        out: list[OptionContract] = []
        for c in contracts:
            q   = quotes_by_symbol.get(c.symbol)
            bid = float(q.bid_price) if q and getattr(q, "bid_price", None) else 0.0
            ask = float(q.ask_price) if q and getattr(q, "ask_price", None) else 0.0

            right  = OptionRight.CALL if c.type == "call" else OptionRight.PUT
            strike = float(c.strike_price)
            expiry = datetime.combine(
                c.expiration_date, datetime.min.time(), tzinfo=timezone.utc
            )
            dte = max(0, (expiry - now_utc).days)

            # Synthesize bid/ask when paper API returns no quote (very common).
            if bid <= 0 and ask <= 0 and spot > 0:
                bid, ask = _estimate_option_price(spot, strike, dte, right)

            est_delta = _estimate_delta(spot, strike, dte, right) if spot > 0 else None

            out.append(OptionContract(
                symbol=c.symbol,
                underlying=underlying,
                expiration=expiry,
                strike=strike,
                right=right,
                bid=bid,
                ask=ask,
                last=(bid + ask) / 2 if bid > 0 and ask > 0 else 0.0,
                open_interest=int(c.open_interest or 0),
                delta=est_delta,
            ))
        return out

    async def get_option_quote(self, occ_symbol: str) -> OptionContract:
        from alpaca.data.requests import OptionLatestQuoteRequest

        req  = OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol)
        try:
            resp = await asyncio.to_thread(
                self._option_data.get_option_latest_quote, req
            )
            q = resp[occ_symbol]
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
        except Exception:
            bid = ask = 0.0

        from app.data.mock_provider import MockMarketDataProvider
        under, exp, right, strike = MockMarketDataProvider._parse_occ(occ_symbol)

        # Synthesize price if quote missing
        if bid <= 0 and ask <= 0:
            now_utc = datetime.now(timezone.utc)
            dte = max(0, (exp - now_utc).days)
            bid, ask = _estimate_option_price(0.0, strike, dte, right)

        return OptionContract(
            symbol=occ_symbol, underlying=under, expiration=exp,
            strike=strike, right=right,
            bid=bid, ask=ask, last=(bid + ask) / 2 if bid > 0 and ask > 0 else 0.0,
        )

    async def get_news(
        self,
        symbols: list[str] | None = None,
        *,
        lookback_hours: float = 6.0,
        limit: int = 20,
    ) -> list[NewsItem]:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest

        if self._news is None:
            self._news = NewsClient(self._key, self._secret)
            harden_alpaca_client(self._news)

        start = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        req = NewsRequest(
            symbols=",".join(symbols) if symbols else None,
            start=start,
            limit=limit,
            sort="desc",
            exclude_contentless=True,
        )
        try:
            resp = await asyncio.to_thread(self._news.get_news, req)
        except Exception as exc:  # noqa: BLE001
            log.warning("alpaca.news_fetch_failed", error=str(exc), symbols=symbols)
            return []

        items = resp.data.get("news", []) if isinstance(resp.data, dict) else (resp.data or [])
        out: list[NewsItem] = []
        for n in items:
            out.append(
                NewsItem(
                    id=str(getattr(n, "id", "")),
                    headline=getattr(n, "headline", "") or "",
                    summary=getattr(n, "summary", "") or "",
                    source=getattr(n, "source", "") or "",
                    url=getattr(n, "url", "") or "",
                    symbols=list(getattr(n, "symbols", []) or []),
                    created_at=_ensure_tz(getattr(n, "created_at", None) or datetime.now(timezone.utc)),
                )
            )
        return out


# ── helpers ───────────────────────────────────────────────────────────────

def _ensure_tz(dt: datetime) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _upcoming_weekly_expirations(now: datetime, count: int = 5) -> list:
    """Return upcoming Friday expirations, including today when applicable."""
    cursor = now.date()
    expirations = []
    while len(expirations) < count:
        days_until_friday = (4 - cursor.weekday()) % 7
        friday = cursor + timedelta(days=days_until_friday)
        if friday not in expirations:
            expirations.append(friday)
        cursor = friday + timedelta(days=1)
    return expirations


def _norm_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation of the standard normal CDF."""
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    pdf  = math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    result = 1.0 - pdf * poly
    return result if x >= 0 else 1.0 - result


def _estimate_option_price(
    spot: float,
    strike: float,
    dte: int,
    right: OptionRight,
    iv: float = 0.35,
    r: float = 0.05,
) -> tuple[float, float]:
    """Black-Scholes synthetic bid/ask for paper-trading when live quotes absent."""
    if spot <= 0:
        intrinsic = 0.05
        return (round(intrinsic * 0.90, 2), round(intrinsic * 1.10 + 0.05, 2))
    if dte <= 0:
        intrinsic = max(0.01, (spot - strike) if right == OptionRight.CALL else (strike - spot))
        return (round(intrinsic * 0.90, 2), round(intrinsic * 1.10, 2))
    try:
        T  = dte / 252.0
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
        disc = math.exp(-r * T)
        if right == OptionRight.CALL:
            price = spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
        else:
            price = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        price = max(0.05, round(price, 2))
        half_spread = max(0.05, round(price * 0.04, 2))  # ~8% synthetic spread
        return (round(price - half_spread, 2), round(price + half_spread, 2))
    except (ValueError, ZeroDivisionError):
        fallback = max(0.05, round(spot * 0.02, 2))
        return (round(fallback * 0.90, 2), round(fallback * 1.10, 2))


def _estimate_delta(
    spot: float,
    strike: float,
    dte: int,
    right: OptionRight,
    iv: float = 0.35,
    r: float = 0.05,
) -> float | None:
    """Estimate Black-Scholes delta when the chain doesn't carry Greeks."""
    if spot <= 0 or dte <= 0:
        return None
    try:
        T  = dte / 252.0
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        delta = _norm_cdf(d1) if right == OptionRight.CALL else _norm_cdf(d1) - 1.0
        return round(delta, 4)
    except (ValueError, ZeroDivisionError):
        return None
