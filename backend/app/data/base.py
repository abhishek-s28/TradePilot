"""Market data provider interface. All providers MUST implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.domain import Bar, NewsItem, OptionContract, Quote


class MarketDataProvider(ABC):
    """Abstract provider. Real-time + historical + options chain."""

    name: str = "abstract"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def is_market_open(self) -> bool: ...

    async def get_market_clock(self) -> dict:
        """Best-effort market clock.

        Providers with an exchange calendar should override this to include
        next open/close. The default keeps the API shape stable for simpler
        providers.
        """
        return _simple_us_equity_clock(await self.is_market_open())

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        timeframe: str,  # e.g. "1Min", "5Min", "1Day"
        start: datetime,
        end: datetime,
    ) -> list[Bar]: ...

    @abstractmethod
    async def get_options_chain(
        self,
        underlying: str,
        expiration: datetime | None = None,
    ) -> list[OptionContract]: ...

    @abstractmethod
    async def get_option_quote(self, occ_symbol: str) -> OptionContract: ...

    async def get_news(
        self,
        symbols: list[str] | None = None,
        *,
        lookback_hours: float = 6.0,
        limit: int = 20,
    ) -> list[NewsItem]:
        """Recent headlines for the given symbols (or market-wide if omitted).

        Default implementation returns nothing — providers without a news feed
        simply opt out, and news-driven strategies treat that as "no signal".
        """
        return []


def _simple_us_equity_clock(is_open: bool) -> dict:
    """NYSE/Nasdaq regular-hours clock fallback.

    This intentionally covers regular weekday sessions only. Alpaca overrides
    this with its official market clock, including holidays and half-days.
    """
    eastern = ZoneInfo("America/New_York")
    now_et = datetime.now(eastern)
    open_t = time(9, 30)
    close_t = time(16, 0)

    today_open = datetime.combine(now_et.date(), open_t, tzinfo=eastern)
    today_close = datetime.combine(now_et.date(), close_t, tzinfo=eastern)

    if _is_weekday(now_et) and today_open <= now_et < today_close:
        next_open = today_open
        next_close = today_close
    elif _is_weekday(now_et) and now_et < today_open:
        next_open = today_open
        next_close = today_close
    else:
        next_open = _next_weekday_session(now_et + timedelta(days=1), open_t)
        next_close = datetime.combine(next_open.date(), close_t, tzinfo=eastern)

    return {
        "is_open": is_open,
        "timestamp": now_et.astimezone(timezone.utc).isoformat(),
        "next_open": next_open.astimezone(timezone.utc).isoformat(),
        "next_close": next_close.astimezone(timezone.utc).isoformat(),
    }


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _next_weekday_session(dt: datetime, open_t: time) -> datetime:
    eastern = ZoneInfo("America/New_York")
    cursor = dt.astimezone(eastern)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return datetime.combine(cursor.date(), open_t, tzinfo=eastern)
