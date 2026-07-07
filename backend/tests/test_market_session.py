from __future__ import annotations

from datetime import datetime, timezone

from app.market.session import MarketSession, classify_us_equity_session, is_us_equity_trading_day


def test_regular_session_classified():
    info = classify_us_equity_session(datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc))
    assert info.session == MarketSession.REGULAR
    assert info.is_equity_tradable
    assert info.is_options_tradable
    assert not info.allows_extended_hours


def test_premarket_classified_stock_only():
    info = classify_us_equity_session(datetime(2026, 5, 22, 11, 0, tzinfo=timezone.utc))
    assert info.session == MarketSession.PREMARKET
    assert info.is_equity_tradable
    assert not info.is_options_tradable
    assert info.allows_extended_hours


def test_afterhours_classified_stock_only():
    info = classify_us_equity_session(datetime(2026, 5, 22, 21, 0, tzinfo=timezone.utc))
    assert info.session == MarketSession.AFTERHOURS
    assert info.is_equity_tradable
    assert not info.is_options_tradable


def test_holiday_closed():
    assert not is_us_equity_trading_day(datetime(2026, 1, 1, tzinfo=timezone.utc).date())
    info = classify_us_equity_session(datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert info.session == MarketSession.CLOSED
