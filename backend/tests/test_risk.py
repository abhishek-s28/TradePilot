from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.domain import AssetClass
from app.risk.manager import (
    AccountSnapshot,
    OrderProposal,
    Position,
    RiskConfig,
    RiskManager,
    RiskState,
)


def _proposal(**overrides) -> OrderProposal:
    data = {
        "strategy_name": "long_directional",
        "legs": ["AAPL240119C00150000"],
        "symbol": "AAPL240119C00150000",
        "underlying": "AAPL",
        "asset_class": AssetClass.OPTION,
        "qty": 1,
        "max_risk_usd": 20.0,
        "est_cost_usd": 35.0,
        "option_premium_per_contract": 35.0,
        "signal_values": {"rsi": 64, "trend": "up"},
        "confidence": 0.72,
    }
    data.update(overrides)
    return OrderProposal(**data)


def _account(**overrides) -> AccountSnapshot:
    data = {
        "cash": 1_000.0,
        "equity": 1_000.0,
        "buying_power": 1_000.0,
        "realized_pnl_today": 0.0,
        "unrealized_pnl_today": 0.0,
    }
    data.update(overrides)
    return AccountSnapshot(**data)


def test_daily_loss_limit_cancels_pending_and_halts_new_entries():
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(realized_pnl_today=-70, unrealized_pnl_today=-31),
        positions=[],
        state=RiskState(daily_realized_pnl=-70, daily_unrealized_pnl=-31),
        config=RiskConfig(max_daily_loss_usd=100),
    )

    assert not decision.approved
    assert decision.reasons == ["daily_loss_limit_hit"]
    assert decision.cancel_pending_orders is True
    assert decision.halt_new_entries_until_next_session is True


def test_single_trade_risk_guard_rejects_above_25():
    decision = RiskManager().evaluate_order(
        _proposal(max_risk_usd=25.01),
        _account(),
        positions=[],
        state=RiskState(),
        config=RiskConfig(max_trade_loss_usd=25),
    )

    assert not decision.approved
    assert any("max_trade_risk_exceeded" in r for r in decision.reasons)


def test_open_position_guard_rejects_at_cap():
    positions = [
        Position(symbol=f"T{i}", qty=1, avg_price=10, current_price=10)
        for i in range(5)
    ]
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(),
        positions=positions,
        state=RiskState(),
        config=RiskConfig(max_open_positions=5),
    )

    assert not decision.approved
    assert "max_open_positions_hit" in decision.reasons


def test_underlying_concentration_rejects_second_option_when_disabled():
    positions = [
        Position(
            symbol="AAPL240119C00160000", qty=1, avg_price=2.0, current_price=2.0,
            asset_class=AssetClass.OPTION,
        )
    ]
    decision = RiskManager().evaluate_order(
        _proposal(),  # symbol="AAPL240119C00150000", underlying="AAPL"
        _account(),
        positions=positions,
        state=RiskState(),
        config=RiskConfig(allow_multiple_option_positions_per_underlying=False),
    )

    assert not decision.approved
    assert any("underlying_concentration:AAPL" in r for r in decision.reasons)


def test_underlying_concentration_can_be_explicitly_allowed():
    positions = [
        Position(
            symbol="AAPL240119C00160000", qty=1, avg_price=2.0, current_price=2.0,
            asset_class=AssetClass.OPTION,
        )
    ]
    decision = RiskManager().evaluate_order(
        _proposal(),  # symbol="AAPL240119C00150000", underlying="AAPL"
        _account(),
        positions=positions,
        state=RiskState(),
        config=RiskConfig(allow_multiple_option_positions_per_underlying=True),
    )

    assert decision.approved
    assert not any("underlying_concentration:AAPL" in r for r in decision.reasons)


def test_underlying_concentration_allows_covered_call_against_stock():
    # An existing STOCK position on the same underlying (e.g. shares held for a
    # covered call / wheel strategy) must not block a new option proposal.
    positions = [
        Position(
            symbol="AAPL", qty=100, avg_price=190.0, current_price=195.0,
            asset_class=AssetClass.STOCK,
        )
    ]
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(),
        positions=positions,
        state=RiskState(),
        config=RiskConfig(),
    )

    assert decision.approved
    assert not any("underlying_concentration" in r for r in decision.reasons)


def test_option_premium_guard_rejects_above_50_per_contract():
    decision = RiskManager().evaluate_order(
        _proposal(option_premium_per_contract=50.01, est_cost_usd=50.01),
        _account(),
        positions=[],
        state=RiskState(),
        config=RiskConfig(max_option_premium_usd=50),
    )

    assert not decision.approved
    assert any("option_premium_limit" in r for r in decision.reasons)


def test_trades_per_day_guard_rejects_at_cap():
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(),
        positions=[],
        state=RiskState(trades_today=20),
        config=RiskConfig(max_trades_per_day=20),
    )

    assert not decision.approved
    assert "max_trades_per_day_hit" in decision.reasons


def test_confidence_floor_rejects_low_quality_order():
    decision = RiskManager().evaluate_order(
        _proposal(confidence=0.62),
        _account(),
        positions=[],
        state=RiskState(market_session="regular"),
        config=RiskConfig(min_confidence_regular=0.68),
    )

    assert not decision.approved
    assert any("confidence_below_floor" in r for r in decision.reasons)


def test_three_loss_cooldown_rejects_for_15_minutes():
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(),
        positions=[],
        state=RiskState(
            consecutive_losses=3,
            last_loss_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
        config=RiskConfig(cooldown_after_losses=3, cooldown_minutes=15),
    )

    assert not decision.approved
    assert "cooldown_active" in decision.reasons


def test_healthy_order_is_approved():
    decision = RiskManager().evaluate_order(
        _proposal(),
        _account(),
        positions=[],
        state=RiskState(),
        config=RiskConfig(),
    )

    assert decision.approved
    assert decision.risk_score > 0
