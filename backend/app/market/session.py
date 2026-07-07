"""US equity market session classification.

The broker clock tells us whether the regular session is open.  The auto-trader
also needs to understand pre-market, after-hours, and overnight equity sessions
so it can keep working around the clock without trying invalid options orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class MarketSession(str, Enum):
    CLOSED = "closed"
    OVERNIGHT = "overnight"
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTERHOURS = "afterhours"


class TradingPhase(str, Enum):
    CLOSED = "closed"
    OVERNIGHT = "overnight"
    PREMARKET = "premarket"
    OPENING_RANGE = "opening_range"
    REGULAR_MIDDAY = "regular_midday"
    POWER_HOUR = "power_hour"
    AFTERHOURS = "afterhours"


@dataclass(frozen=True)
class MarketSessionInfo:
    session: MarketSession
    phase: TradingPhase
    now_et: datetime
    trading_day: date | None
    is_trading_day: bool
    is_equity_tradable: bool
    is_options_tradable: bool
    allows_extended_hours: bool
    reason: str = ""

    @property
    def label(self) -> str:
        return self.session.value

    @property
    def is_regular(self) -> bool:
        return self.session == MarketSession.REGULAR

    @property
    def can_open_stock(self) -> bool:
        return self.is_equity_tradable

    @property
    def can_open_option(self) -> bool:
        return self.is_options_tradable


def classify_us_equity_session(now: datetime | None = None) -> MarketSessionInfo:
    """Classify the current US equity session.

    Regular-session holidays are handled locally so the scheduler can make a
    good decision even before the provider or broker is connected.  Alpaca's
    clock remains the authority for exact exchange status in the health API.
    """
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    d = now_et.date()
    t = now_et.time()

    today_is_trading = is_us_equity_trading_day(d)
    close_t = regular_close_time(d)

    if today_is_trading and time(4, 0) <= t < time(9, 30):
        return MarketSessionInfo(
            session=MarketSession.PREMARKET,
            phase=TradingPhase.PREMARKET,
            now_et=now_et,
            trading_day=d,
            is_trading_day=True,
            is_equity_tradable=True,
            is_options_tradable=False,
            allows_extended_hours=True,
        )

    if today_is_trading and time(9, 30) <= t < close_t:
        if t < time(10, 30):
            phase = TradingPhase.OPENING_RANGE
        elif t >= time(15, 0) and close_t > time(15, 0):
            phase = TradingPhase.POWER_HOUR
        else:
            phase = TradingPhase.REGULAR_MIDDAY
        return MarketSessionInfo(
            session=MarketSession.REGULAR,
            phase=phase,
            now_et=now_et,
            trading_day=d,
            is_trading_day=True,
            is_equity_tradable=True,
            is_options_tradable=True,
            allows_extended_hours=False,
        )

    if today_is_trading and close_t <= t < time(20, 0):
        return MarketSessionInfo(
            session=MarketSession.AFTERHOURS,
            phase=TradingPhase.AFTERHOURS,
            now_et=now_et,
            trading_day=d,
            is_trading_day=True,
            is_equity_tradable=True,
            is_options_tradable=False,
            allows_extended_hours=True,
        )

    if _is_overnight_equity_session(now_et):
        trading_day = d if t < time(4, 0) else _next_trading_day(d + timedelta(days=1))
        return MarketSessionInfo(
            session=MarketSession.OVERNIGHT,
            phase=TradingPhase.OVERNIGHT,
            now_et=now_et,
            trading_day=trading_day,
            is_trading_day=bool(trading_day),
            is_equity_tradable=True,
            is_options_tradable=False,
            allows_extended_hours=True,
        )

    return MarketSessionInfo(
        session=MarketSession.CLOSED,
        phase=TradingPhase.CLOSED,
        now_et=now_et,
        trading_day=d if today_is_trading else None,
        is_trading_day=today_is_trading,
        is_equity_tradable=False,
        is_options_tradable=False,
        allows_extended_hours=False,
        reason="outside_equity_trading_sessions",
    )


def is_us_equity_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _nyse_holidays(d.year)


def regular_close_time(d: date) -> time:
    return time(13, 0) if _is_early_close(d) else time(16, 0)


def _is_overnight_equity_session(now_et: datetime) -> bool:
    d = now_et.date()
    t = now_et.time()

    if t < time(4, 0):
        return is_us_equity_trading_day(d)

    if t >= time(20, 0):
        next_day = _next_trading_day(d + timedelta(days=1))
        return next_day is not None and (next_day - d).days == 1

    return False


def _next_trading_day(start: date) -> date | None:
    cursor = start
    for _ in range(10):
        if is_us_equity_trading_day(cursor):
            return cursor
        cursor += timedelta(days=1)
    return None


def _nyse_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Presidents Day
        _western_easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),     # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),   # Independence Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    # Observed New Year's Day can fall on Dec 31 of the prior year.
    holidays.add(_observed(date(year + 1, 1, 1)))
    return {h for h in holidays if h.year == year}


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    cursor = date(year, month, 1)
    while cursor.weekday() != weekday:
        cursor += timedelta(days=1)
    return cursor + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _western_easter(year: int) -> date:
    """Gregorian Easter date, valid for modern NYSE calendars."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _is_early_close(d: date) -> bool:
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24 and d.weekday() < 5:
        return True
    if d.month == 7 and d.day == 3 and d.weekday() < 5:
        return True
    return False
