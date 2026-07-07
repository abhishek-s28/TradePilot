from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.auto_trade.option_exit import evaluate_option_exit
from app.models.domain import AssetClass, Position


NY = ZoneInfo("America/New_York")


def _pos(symbol: str, *, avg=1.0, current=1.0, pnl=0.0) -> Position:
    return Position(
        symbol=symbol,
        asset_class=AssetClass.OPTION,
        qty=1,
        avg_price=avg,
        current_price=current,
        unrealized_pnl=pnl,
        realized_pnl=0.0,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )


def test_option_exit_profit_target():
    now = datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc)
    decision = evaluate_option_exit(
        _pos("AAPL260626C00190000", avg=1.0, current=1.6, pnl=60.0),
        opened_at=now - timedelta(minutes=30),
        now=now,
    )

    assert decision.should_exit is True
    assert decision.reason == "profit_target"


def test_option_exit_hard_stop_loss():
    now = datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc)
    decision = evaluate_option_exit(
        _pos("AAPL260626C00190000", avg=1.0, current=0.6, pnl=-40.0),
        opened_at=now - timedelta(minutes=30),
        now=now,
    )

    assert decision.should_exit is True
    assert decision.reason == "hard_stop_loss"


def test_option_exit_trailing_profit_protect():
    now = datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc)
    decision = evaluate_option_exit(
        _pos("AAPL260626C00190000", avg=1.0, current=1.15, pnl=15.0),
        opened_at=now - timedelta(minutes=30),
        previous_peak_pnl_pct=0.34,
        now=now,
    )

    assert decision.should_exit is True
    assert decision.reason == "trailing_profit_protect"


def test_option_exit_expiration_day_cutoff():
    now = datetime(2026, 6, 11, 15, 31, tzinfo=NY).astimezone(timezone.utc)
    decision = evaluate_option_exit(
        _pos("AAPL260611C00190000", avg=1.0, current=1.05, pnl=5.0),
        opened_at=now - timedelta(minutes=30),
        now=now,
    )

    assert decision.should_exit is True
    assert decision.reason == "expiration_day_cutoff"


def test_option_exit_holds_before_min_hold_without_severe_loss():
    now = datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc)
    decision = evaluate_option_exit(
        _pos("AAPL260626C00190000", avg=1.0, current=1.1, pnl=10.0),
        opened_at=now - timedelta(minutes=2),
        now=now,
    )

    assert decision.should_exit is False
    assert decision.reason == "min_hold"
