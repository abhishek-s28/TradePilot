"""Loader: hydrate the in-memory RiskConfig from DB-backed RiskSettings.

The RiskManager is stateless and takes a dataclass config. The UI persists user
settings (including the kill switch) into the risk_settings table. This module
bridges the two so the kill switch and edited limits actually take effect.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.settings import get_settings
from app.database.models import RiskSettings
from app.database.session import session_factory
from app.risk.manager import RiskConfig

_NON_LIVE_CAPS: dict[str, float | int] = {
    "max_daily_loss_usd": 1500.0,
    "max_trade_loss_usd": 500.0,
    "max_open_positions": 20,
    "max_trades_per_day": 80,
    "max_option_premium_usd": 500.0,
}

# Percent-of-equity caps for paper accounts. The fixed dollar ceilings above
# were sized for a much larger account balance; once equity shrinks (or grows)
# a fixed dollar risk per trade stops representing a sane fraction of the
# account. These percentages take over as the binding constraint while
# _NON_LIVE_CAPS continues to act as an absolute ceiling either way.
_NON_LIVE_CAP_PCTS: dict[str, float] = {
    "max_daily_loss_usd": 1.00,
    "max_trade_loss_usd": 1.00,
    "max_option_premium_usd": 1.00,
}
_NON_LIVE_CAP_FLOORS: dict[str, float] = {
    "max_daily_loss_usd": 100.0,
    "max_trade_loss_usd": 50.0,
    "max_option_premium_usd": 50.0,
}


async def load_risk_config(equity: float | None = None) -> RiskConfig:
    """Build a RiskConfig from the persisted RiskSettings row, falling back to
    the .env defaults when no row exists yet.

    `equity` (live account equity), when provided, scales the non-live safety
    caps to the current account size — see `_NON_LIVE_CAP_PCTS`.
    """
    s = get_settings()
    cfg = RiskConfig(
        max_daily_loss_usd=s.risk_max_daily_loss_usd,
        max_trade_loss_usd=s.risk_max_trade_loss_usd,
        max_open_positions=s.risk_max_open_positions,
        max_trades_per_day=s.risk_max_trades_per_day,
        max_option_premium_usd=s.risk_max_option_premium_usd,
        cooldown_after_losses=s.risk_cooldown_after_losses,
        allow_extended_hours_options=s.auto_trade_options_extended_hours,
        min_confidence_regular=s.auto_trade_regular_min_confidence,
        min_confidence_extended=s.auto_trade_extended_min_confidence,
        min_confidence_overnight=s.auto_trade_overnight_min_confidence,
    )
    async with session_factory()() as session:
        res = await session.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
    if row is None:
        return _apply_non_live_safety_caps(cfg, s, equity)
    persisted = RiskConfig(
        max_daily_loss_usd=row.max_daily_loss_usd,
        max_trade_loss_usd=row.max_trade_loss_usd,
        max_open_positions=row.max_open_positions,
        max_trades_per_day=row.max_trades_per_day,
        max_option_premium_usd=row.max_option_premium_usd,
        cooldown_after_losses=row.cooldown_after_losses,
        allowed_strategies=row.allowed_strategies or [],
        allowed_tickers=row.allowed_tickers or [],
        kill_switch_active=row.kill_switch_active,
        # Internal-only fields. Keep data fresh enough for delayed paper data,
        # but do not relax spreads or confidence into lottery-ticket territory.
        max_weekly_loss_usd=max(row.max_daily_loss_usd * 3, cfg.max_weekly_loss_usd),
        max_position_value_usd=cfg.max_position_value_usd,
        max_portfolio_allocation_pct=cfg.max_portfolio_allocation_pct,
        max_ticker_concentration_pct=cfg.max_ticker_concentration_pct,
        cooldown_minutes=cfg.cooldown_minutes,
        max_signal_age_seconds=300,   # 5 min — matches scheduler interval
        max_quote_age_seconds=1800,   # 30 min — covers Alpaca IEX 15-min delay
        max_spread_pct_stock=0.02,
        max_spread_pct_option=0.12,
        allow_extended_hours_stocks=s.auto_trade_premarket or s.auto_trade_afterhours,
        allow_extended_hours_options=s.auto_trade_options_extended_hours,
        allow_multiple_option_positions_per_underlying=True,
        min_confidence_regular=s.auto_trade_regular_min_confidence,
        min_confidence_extended=s.auto_trade_extended_min_confidence,
        min_confidence_overnight=s.auto_trade_overnight_min_confidence,
        block_around_earnings=cfg.block_around_earnings,
    )
    return _apply_non_live_safety_caps(persisted, s, equity)


def _apply_non_live_safety_caps(cfg: RiskConfig, settings, equity: float | None = None) -> RiskConfig:
    """Keep stale DB/UI settings from making automation too aggressive.

    In non-live mode we cap trade count and premium upward drift to the current
    options-paper profile so stale DB/UI settings cannot silently override the
    intended automation envelope. Live-capable mode still uses the exact
    persisted limits.
    """
    caps = non_live_risk_caps(settings, equity)
    if not caps:
        return cfg
    return cfg.__class__(
        **{
            **cfg.__dict__,
            "max_daily_loss_usd": min(cfg.max_daily_loss_usd, caps["max_daily_loss_usd"]),
            "max_trade_loss_usd": min(cfg.max_trade_loss_usd, caps["max_trade_loss_usd"]),
            "max_open_positions": min(cfg.max_open_positions, caps["max_open_positions"]),
            "max_trades_per_day": min(cfg.max_trades_per_day, caps["max_trades_per_day"]),
            "max_option_premium_usd": min(cfg.max_option_premium_usd, caps["max_option_premium_usd"]),
            "max_spread_pct_stock": min(cfg.max_spread_pct_stock, 0.02),
            "max_spread_pct_option": min(cfg.max_spread_pct_option, 0.12),
            "max_position_value_usd": min(cfg.max_position_value_usd, equity * 0.20) if equity else cfg.max_position_value_usd,
            "max_ticker_concentration_pct": min(cfg.max_ticker_concentration_pct, 0.20),
            "allow_extended_hours_stocks": False,
            "allow_extended_hours_options": False,
            "allow_multiple_option_positions_per_underlying": True,
            "min_confidence_regular": max(cfg.min_confidence_regular, 0.62),
            "min_confidence_extended": max(cfg.min_confidence_extended, 0.75),
            "min_confidence_overnight": max(cfg.min_confidence_overnight, 0.85),
        }
    )


def non_live_risk_caps(settings, equity: float | None = None) -> dict[str, float | int]:
    if settings.can_trade_live:
        return {}
    caps = dict(_NON_LIVE_CAPS)
    if equity is not None and equity > 0:
        for key, pct in _NON_LIVE_CAP_PCTS.items():
            scaled = max(equity * pct, _NON_LIVE_CAP_FLOORS[key])
            caps[key] = min(caps[key], scaled)
    return caps
