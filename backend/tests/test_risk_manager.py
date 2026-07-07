"""Risk manager tests. The most important tests in this codebase."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.domain import (
    AccountSnapshot,
    AssetClass,
    Direction,
    Position,
    Quote,
    Signal,
    SignalStatus,
)
from app.risk.manager import RiskConfig, RiskManager, RiskState
from app.risk.manager import OrderProposal as RiskOrderProposal


def _sig(**over) -> Signal:
    base = dict(
        strategy="test", asset_class=AssetClass.STOCK, symbol="AAPL",
        direction=Direction.BULLISH, entry=100.0, stop_loss=98.0,
        take_profit=104.0, confidence=0.7, reason="test",
        generated_at=datetime.now(timezone.utc), status=SignalStatus.NEW,
    )
    base.update(over)
    return Signal(**base)


def _quote(price=100.0, spread=0.05, now=None) -> Quote:
    now = now or datetime.now(timezone.utc)
    return Quote(symbol="AAPL", bid=price - spread / 2, ask=price + spread / 2,
                 last=price, timestamp=now)


def _account(cash=10_000.0, equity=10_000.0) -> AccountSnapshot:
    return AccountSnapshot(
        cash=cash, equity=equity, buying_power=cash, positions_value=0.0,
    )


def test_happy_path_approves():
    rm = RiskManager()
    d = rm.evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(), config=RiskConfig(),
    )
    assert d.approved, d.reasons
    assert d.proposal is not None
    assert d.proposal.qty >= 1


def test_kill_switch_blocks():
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(), config=RiskConfig(kill_switch_active=True),
    )
    assert not d.approved
    assert "kill_switch_active" in d.reasons


def test_stale_signal_blocked():
    old = datetime.now(timezone.utc) - timedelta(seconds=300)
    d = RiskManager().evaluate_signal(
        signal=_sig(generated_at=old), quote=_quote(),
        account=_account(), state=RiskState(), config=RiskConfig(),
    )
    assert not d.approved
    assert any("signal_stale" in r for r in d.reasons)


def test_stale_quote_blocked():
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(now=old),
        account=_account(), state=RiskState(), config=RiskConfig(),
    )
    assert not d.approved
    assert any("quote_stale" in r for r in d.reasons)


def test_wide_spread_blocked():
    # 2% spread on a $100 stock
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(spread=2.0),
        account=_account(), state=RiskState(), config=RiskConfig(),
    )
    assert not d.approved
    assert any("spread_too_wide" in r for r in d.reasons)


def test_max_daily_loss_blocked():
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(daily_realized_pnl=-600), config=RiskConfig(),
    )
    assert not d.approved
    assert "max_daily_loss_hit" in d.reasons


def test_max_open_positions_blocked():
    positions = [
        Position(symbol=f"X{i}", asset_class=AssetClass.STOCK, qty=1,
                 avg_price=10, current_price=10, unrealized_pnl=0,
                 opened_at=datetime.now(timezone.utc))
        for i in range(5)
    ]
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(open_positions=positions),
        config=RiskConfig(max_open_positions=5),
    )
    assert not d.approved
    assert "max_open_positions_hit" in d.reasons


def test_duplicate_position_blocked():
    pos = [Position(symbol="AAPL", asset_class=AssetClass.STOCK, qty=10,
                    avg_price=100, current_price=100, unrealized_pnl=0,
                    opened_at=datetime.now(timezone.utc))]
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(open_positions=pos), config=RiskConfig(),
    )
    assert not d.approved
    assert "duplicate_open_position" in d.reasons


def test_multiple_option_contracts_same_underlying_allowed():
    pos = [Position(symbol="AAPL260626C00325000", asset_class=AssetClass.OPTION, qty=1,
                    avg_price=2.0, current_price=2.0, unrealized_pnl=0,
                    opened_at=datetime.now(timezone.utc))]
    proposal = RiskOrderProposal(
        strategy_name="test",
        legs=["AAPL260626P00290000"],
        symbol="AAPL260626P00290000",
        underlying="AAPL",
        asset_class=AssetClass.OPTION,
        max_risk_usd=125,
        est_cost_usd=125,
        signal_values={},
        confidence=0.75,
    )

    d = RiskManager().evaluate_order(
        proposal=proposal,
        account=_account(cash=10_000),
        positions=pos,
        state=RiskState(),
        config=RiskConfig(
            max_trade_loss_usd=500,
            max_option_premium_usd=500,
            allow_multiple_option_positions_per_underlying=True,
        ),
    )

    assert d.approved, d.reasons


def test_qty_capped_by_max_trade_loss():
    # $20 risk per share, $100 max trade loss → qty=5 max
    d = RiskManager().evaluate_signal(
        signal=_sig(entry=100, stop_loss=80, suggested_qty=100),
        quote=_quote(price=100), account=_account(cash=100_000),
        state=RiskState(),
        config=RiskConfig(max_trade_loss_usd=100, max_position_value_usd=100_000),
    )
    assert d.approved
    assert d.proposal.qty == 5


def test_invalid_stop_blocked():
    d = RiskManager().evaluate_signal(
        signal=_sig(entry=100, stop_loss=105),
        quote=_quote(), account=_account(),
        state=RiskState(), config=RiskConfig(),
    )
    assert not d.approved
    assert "invalid_stop_loss" in d.reasons


def test_cooldown_blocks_after_losses():
    d = RiskManager().evaluate_signal(
        signal=_sig(), quote=_quote(), account=_account(),
        state=RiskState(
            consecutive_losses=3,
            last_loss_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
        config=RiskConfig(cooldown_after_losses=3, cooldown_minutes=30),
    )
    assert not d.approved
    assert "cooldown_active" in d.reasons


def test_allowed_tickers_filter():
    d = RiskManager().evaluate_signal(
        signal=_sig(symbol="MEME"), quote=_quote(),
        account=_account(), state=RiskState(),
        config=RiskConfig(allowed_tickers=["AAPL", "MSFT"]),
    )
    assert not d.approved
    assert any("ticker_not_allowed" in r for r in d.reasons)


def test_option_premium_cap():
    # ATM call worth $5 → max premium $200 → qty capped at 0... or no?
    # $5*100 = $500 per contract, max premium $200 → qty 0 → rejected
    d = RiskManager().evaluate_signal(
        signal=_sig(asset_class=AssetClass.OPTION, symbol="AAPL240119C00100000",
                   entry=5.0, stop_loss=2.0, take_profit=10.0),
        quote=Quote(symbol="AAPL240119C00100000", bid=4.95, ask=5.05, last=5.0,
                    timestamp=datetime.now(timezone.utc)),
        account=_account(cash=10_000),
        state=RiskState(),
        config=RiskConfig(
            max_option_premium_usd=200, max_trade_loss_usd=1000,
            max_position_value_usd=10_000, max_spread_pct_option=0.10,
        ),
    )
    assert not d.approved
    assert "qty_capped_to_zero" in d.reasons


def test_short_stock_disabled():
    d = RiskManager().evaluate_signal(
        signal=_sig(direction=Direction.BEARISH),
        quote=_quote(), account=_account(),
        state=RiskState(), config=RiskConfig(),
    )
    assert not d.approved
    assert "short_stock_disabled" in d.reasons
