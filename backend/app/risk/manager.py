"""Central risk gate for every automated order.

No strategy or broker adapter is allowed to bypass this module.  Strategies
produce OrderProposal objects, the auto-trade loop asks RiskManager, and only an
approved decision may reach the broker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.models.domain import (
    AccountSnapshot as DomainAccountSnapshot,
    AssetClass,
    Direction,
    OrderProposal as DomainOrderProposal,
    OrderType,
    Quote,
    Side,
    Signal,
    TimeInForce,
)

log = get_logger(__name__)


@dataclass
class AccountSnapshot:
    cash: float
    equity: float
    buying_power: float
    realized_pnl_today: float = 0.0
    unrealized_pnl_today: float = 0.0


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    asset_class: AssetClass = AssetClass.STOCK


@dataclass
class OrderProposal:
    strategy_name: str
    legs: list[str]
    max_risk_usd: float
    est_cost_usd: float
    signal_values: dict[str, Any]
    confidence: float
    symbol: str = ""
    underlying: str | None = None
    asset_class: AssetClass = AssetClass.STOCK
    side: Side = Side.BUY
    qty: int = 1
    limit_price: float | None = None
    option_premium_per_contract: float | None = None
    reason: str = ""
    extended_hours: bool = False


@dataclass
class RiskConfig:
    max_daily_loss_usd: float = 100.0
    max_weekly_loss_usd: float = 1500.0
    max_trade_loss_usd: float = 25.0
    max_position_value_usd: float = 5_000.0
    max_portfolio_allocation_pct: float = 0.95
    max_open_positions: int = 20
    max_trades_per_day: int = 80
    max_option_premium_usd: float = 50.0
    max_ticker_concentration_pct: float = 0.25
    cooldown_after_losses: int = 3
    cooldown_minutes: int = 15
    allowed_strategies: list[str] = field(default_factory=list)
    allowed_tickers: list[str] = field(default_factory=list)
    kill_switch_active: bool = False
    max_signal_age_seconds: int = 300
    max_quote_age_seconds: int = 60
    max_spread_pct_stock: float = 0.01
    max_spread_pct_option: float = 0.15
    allow_extended_hours_stocks: bool = True
    allow_extended_hours_options: bool = False
    allow_multiple_option_positions_per_underlying: bool = True
    min_confidence_regular: float = 0.62
    min_confidence_extended: float = 0.75
    min_confidence_overnight: float = 0.88
    block_around_earnings: bool = False


@dataclass
class RiskState:
    daily_realized_pnl: float = 0.0
    daily_unrealized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    open_positions: list[Any] = field(default_factory=list)
    trades_today: int = 0
    consecutive_losses: int = 0
    last_loss_at: datetime | None = None
    pending_symbols: set[str] = field(default_factory=set)
    upcoming_earnings_symbols: set[str] = field(default_factory=set)
    market_regime: str = "unknown"
    market_session: str = "regular"
    halted_until_next_session: bool = False


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    proposal: Any | None = None
    risk_score: float = 0.0
    cancel_pending_orders: bool = False
    halt_new_entries_until_next_session: bool = False

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"


class RiskManager:
    """Stateless evaluator.  All live state is passed in by the caller."""

    def evaluate_order(
        self,
        proposal: OrderProposal,
        account: AccountSnapshot | DomainAccountSnapshot,
        positions: list[Any],
        state: RiskState,
        config: RiskConfig,
        now: datetime | None = None,
    ) -> RiskDecision:
        now = _aware(now or datetime.now(timezone.utc))
        reasons: list[str] = []
        is_closing = proposal.signal_values.get("intent") == "close"

        if config.kill_switch_active:
            reasons.append("kill_switch_active")
        if not is_closing and state.halted_until_next_session:
            reasons.append("halted_until_next_session")

        daily_pnl = _daily_pnl(account, state)
        if daily_pnl <= -abs(config.max_daily_loss_usd):
            log.critical(
                "DAILY LOSS LIMIT HIT",
                daily_pnl=daily_pnl,
                max_daily_loss_usd=config.max_daily_loss_usd,
            )
            return RiskDecision(
                approved=False,
                reasons=["daily_loss_limit_hit"],
                proposal=proposal,
                risk_score=1.0,
                cancel_pending_orders=True,
                halt_new_entries_until_next_session=True,
            )

        confidence_floor = _min_confidence_for_session(config, state.market_session)
        if not is_closing and proposal.confidence < confidence_floor:
            reasons.append(
                f"confidence_below_floor:{proposal.confidence:.2f}<{confidence_floor:.2f}"
            )

        if proposal.max_risk_usd > config.max_trade_loss_usd:
            reasons.append(
                f"max_trade_risk_exceeded:{proposal.max_risk_usd:.2f}>{config.max_trade_loss_usd:.2f}"
            )

        if not is_closing and len(positions) >= config.max_open_positions:
            reasons.append("max_open_positions_hit")

        premium = proposal.option_premium_per_contract
        if premium is None and proposal.asset_class == AssetClass.OPTION:
            premium = _option_premium_from_cost(proposal)
        if (
            not is_closing
            and premium is not None
            and premium > config.max_option_premium_usd
        ):
            reasons.append(
                f"option_premium_limit:{premium:.2f}>{config.max_option_premium_usd:.2f}"
            )

        if not is_closing and state.trades_today >= config.max_trades_per_day:
            reasons.append("max_trades_per_day_hit")

        last_loss_at = _aware(state.last_loss_at) if state.last_loss_at else None
        if (
            state.consecutive_losses >= config.cooldown_after_losses
            and last_loss_at is not None
            and now - last_loss_at < timedelta(minutes=config.cooldown_minutes)
        ):
            reasons.append("cooldown_active")

        if proposal.strategy_name and config.allowed_strategies:
            if proposal.strategy_name not in config.allowed_strategies:
                reasons.append(f"strategy_not_allowed:{proposal.strategy_name}")

        underlying = proposal.underlying or proposal.symbol
        if underlying and config.allowed_tickers and underlying not in config.allowed_tickers:
            reasons.append(f"ticker_not_allowed:{underlying}")

        if (
            not is_closing
            and proposal.asset_class == AssetClass.OPTION
            and state.market_session != "regular"
        ):
            reasons.append("options_regular_hours_only")
        if not is_closing:
            if any(getattr(p, "symbol", "") == proposal.symbol for p in positions):
                reasons.append("duplicate_open_position")
            elif (
                not config.allow_multiple_option_positions_per_underlying
                and proposal.asset_class == AssetClass.OPTION
                and any(
                    getattr(p, "asset_class", None) == AssetClass.OPTION
                    and _underlying_of(p) == underlying
                    for p in positions
                )
            ):
                reasons.append(f"underlying_concentration:{underlying}")
            if proposal.symbol in state.pending_symbols:
                reasons.append("duplicate_pending_order")
            if underlying in state.pending_symbols:
                reasons.append(f"duplicate_pending_underlying:{underlying}")
            if proposal.side == Side.SELL and proposal.asset_class == AssetClass.STOCK:
                reasons.append("short_stock_disabled")

        if reasons:
            log.info(
                "risk.rejected",
                strategy=proposal.strategy_name,
                symbol=proposal.symbol or proposal.legs,
                reasons=reasons,
                max_risk_usd=proposal.max_risk_usd,
                est_cost_usd=proposal.est_cost_usd,
                confidence=proposal.confidence,
                signal_values=proposal.signal_values,
            )
            return RiskDecision(False, reasons, proposal=proposal, risk_score=1.0)

        risk_score = min(
            1.0,
            (proposal.max_risk_usd / max(config.max_trade_loss_usd, 1.0)) * 0.6
            + (1.0 - proposal.confidence) * 0.4,
        )
        log.info(
            "risk.approved",
            strategy=proposal.strategy_name,
            symbol=proposal.symbol or proposal.legs,
            max_risk_usd=proposal.max_risk_usd,
            est_cost_usd=proposal.est_cost_usd,
            confidence=proposal.confidence,
            risk_score=round(risk_score, 3),
            signal_values=proposal.signal_values,
        )
        return RiskDecision(True, [], proposal=proposal, risk_score=round(risk_score, 3))

    def evaluate_signal(
        self,
        signal: Signal,
        quote: Quote,
        account: DomainAccountSnapshot,
        state: RiskState,
        config: RiskConfig,
        now: datetime | None = None,
    ) -> RiskDecision:
        """Compatibility path for the existing signal-service API."""
        now = _aware(now or datetime.now(timezone.utc))
        reasons: list[str] = []

        if config.kill_switch_active:
            reasons.append("kill_switch_active")
        if not signal:
            return RiskDecision(False, ["no_signal"])
        if state.halted_until_next_session:
            reasons.append("halted_until_next_session")

        sig_ts = _aware(signal.generated_at)
        if (now - sig_ts).total_seconds() > config.max_signal_age_seconds:
            reasons.append(f"signal_stale>{config.max_signal_age_seconds}s")
        if quote.is_stale(config.max_quote_age_seconds, now):
            reasons.append(f"quote_stale>{config.max_quote_age_seconds}s")

        if config.allowed_strategies and signal.strategy not in config.allowed_strategies:
            reasons.append(f"strategy_not_allowed:{signal.strategy}")

        sym_key = signal.underlying or signal.symbol
        if config.allowed_tickers and sym_key not in config.allowed_tickers:
            reasons.append(f"ticker_not_allowed:{sym_key}")
        if config.block_around_earnings and sym_key in state.upcoming_earnings_symbols:
            reasons.append("earnings_blackout")

        total_daily_pnl = state.daily_realized_pnl + state.daily_unrealized_pnl
        if total_daily_pnl <= -abs(config.max_daily_loss_usd):
            log.critical(
                "DAILY LOSS LIMIT HIT",
                daily_pnl=total_daily_pnl,
                max_daily_loss_usd=config.max_daily_loss_usd,
            )
            reasons.extend(["max_daily_loss_hit", "daily_loss_limit_hit"])

        if state.weekly_realized_pnl <= -abs(config.max_weekly_loss_usd):
            reasons.append("max_weekly_loss_hit")
        if state.trades_today >= config.max_trades_per_day:
            reasons.append("max_trades_per_day_hit")

        confidence_floor = _min_confidence_for_session(config, state.market_session)
        if signal.confidence < confidence_floor:
            reasons.append(
                f"confidence_below_floor:{signal.confidence:.2f}<{confidence_floor:.2f}"
            )

        last_loss = _aware(state.last_loss_at) if state.last_loss_at else None
        if (
            state.consecutive_losses >= config.cooldown_after_losses
            and last_loss is not None
            and now - last_loss < timedelta(minutes=config.cooldown_minutes)
        ):
            reasons.append("cooldown_active")

        is_option = signal.asset_class == AssetClass.OPTION
        if is_option and state.market_session != "regular" and not config.allow_extended_hours_options:
            reasons.append("options_regular_hours_only")
        if not is_option and state.market_session != "regular" and not config.allow_extended_hours_stocks:
            reasons.append("stocks_extended_hours_disabled")

        max_spread = config.max_spread_pct_option if is_option else config.max_spread_pct_stock
        if quote.spread_pct > max_spread:
            reasons.append(f"spread_too_wide:{quote.spread_pct:.3f}>{max_spread}")

        if len(state.open_positions) >= config.max_open_positions:
            reasons.append("max_open_positions_hit")
        if any(getattr(p, "symbol", "") == signal.symbol for p in state.open_positions):
            reasons.append("duplicate_open_position")
        if signal.symbol in state.pending_symbols:
            reasons.append("duplicate_pending_order")
        if sym_key in state.pending_symbols:
            reasons.append(f"duplicate_pending_underlying:{sym_key}")
        if (
            is_option
            and not config.allow_multiple_option_positions_per_underlying
            and any(
                getattr(p, "asset_class", None) == AssetClass.OPTION
                and _underlying_of(p) == sym_key
                for p in state.open_positions
            )
        ):
            reasons.append(f"underlying_concentration:{sym_key}")

        if signal.direction == Direction.BEARISH and not is_option:
            reasons.append("short_stock_disabled")

        entry = float(signal.entry)
        risk_per_unit = entry - float(signal.stop_loss)
        if risk_per_unit <= 0:
            reasons.append("invalid_stop_loss")
            return RiskDecision(False, reasons, risk_score=1.0)

        contract_multiplier = 100 if is_option else 1
        max_qty_by_trade_loss = int(config.max_trade_loss_usd // (risk_per_unit * contract_multiplier))
        max_qty_by_value = int(config.max_position_value_usd // max(entry * contract_multiplier, 0.01))
        max_qty_by_bp = int((account.buying_power * 0.95) // max(entry * contract_multiplier, 0.01))
        max_qty_by_premium = (
            int(config.max_option_premium_usd // max(entry * 100, 0.01))
            if is_option
            else 10_000
        )

        qty = min(
            signal.suggested_qty or 1,
            max_qty_by_trade_loss,
            max_qty_by_value,
            max_qty_by_bp,
            max_qty_by_premium,
        )
        if qty <= 0:
            reasons.append("qty_capped_to_zero")

        if reasons:
            log.info("risk.rejected", symbol=signal.symbol, strategy=signal.strategy, reasons=reasons)
            return RiskDecision(
                False,
                reasons,
                risk_score=1.0,
                cancel_pending_orders="daily_loss_limit_hit" in reasons,
                halt_new_entries_until_next_session="daily_loss_limit_hit" in reasons,
            )

        est_cost = round(entry * qty * contract_multiplier, 2)
        est_max_loss = round(risk_per_unit * qty * contract_multiplier, 2)
        risk_score = min(
            1.0,
            (est_max_loss / max(config.max_trade_loss_usd, 1.0)) * 0.6
            + (1.0 - signal.confidence) * 0.4,
        )
        proposal = DomainOrderProposal(
            signal_id=signal.id,
            symbol=signal.symbol,
            asset_class=signal.asset_class,
            side=Side.BUY,
            qty=qty,
            order_type=OrderType.LIMIT,
            limit_price=round(entry, 2),
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            time_in_force=TimeInForce.DAY,
            extended_hours=state.market_session != "regular" and not is_option,
            estimated_cost=est_cost,
            estimated_max_loss=est_max_loss,
            reason=signal.reason,
            risk_score=round(risk_score, 3),
            strategy_name=signal.strategy,
            legs=[signal.symbol],
            max_risk_usd=est_max_loss,
            est_cost_usd=est_cost,
            signal_values=signal.metadata,
            confidence=signal.confidence,
        )
        log.info(
            "risk.approved",
            symbol=signal.symbol,
            strategy=signal.strategy,
            qty=qty,
            max_risk_usd=est_max_loss,
            est_cost_usd=est_cost,
            confidence=signal.confidence,
            risk_score=round(risk_score, 3),
            signal_values=signal.metadata,
        )
        return RiskDecision(True, [], proposal=proposal, risk_score=round(risk_score, 3))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _daily_pnl(account: AccountSnapshot | DomainAccountSnapshot, state: RiskState) -> float:
    realized = getattr(account, "realized_pnl_today", state.daily_realized_pnl)
    unrealized = getattr(account, "unrealized_pnl_today", state.daily_unrealized_pnl)
    if realized == 0.0 and unrealized == 0.0 and getattr(account, "daily_pnl", 0.0):
        return float(getattr(account, "daily_pnl"))
    return float(realized or 0.0) + float(unrealized or 0.0)


def _min_confidence_for_session(config: RiskConfig, session: str) -> float:
    if session in {"premarket", "afterhours"}:
        return float(config.min_confidence_extended)
    if session == "overnight":
        return float(config.min_confidence_overnight)
    return float(config.min_confidence_regular)


def _option_premium_from_cost(proposal: OrderProposal) -> float | None:
    if not proposal.legs or proposal.qty <= 0:
        return None
    return proposal.est_cost_usd / proposal.qty


def _underlying_of(position: Any) -> str:
    """Underlying ticker for a position.

    Stock positions are keyed by the ticker itself. OCC option symbols encode
    the underlying as everything before the trailing 15-char date+right+strike
    block (e.g. "AAPL260626C00325000" -> "AAPL").
    """
    symbol = getattr(position, "symbol", "") or ""
    if getattr(position, "asset_class", None) != AssetClass.OPTION:
        return symbol
    return symbol[:-15] or symbol
