"""Option position exit policy — think like a disciplined day trader.

Principles:
  1. Let winners RUN — options can move 50-200% in a session.
  2. Cut losers with conviction — but not so tight that bid-ask noise kills you.
  3. Trail dynamically — lock in a % of peak gains that SCALES with profit size.
  4. Time is the enemy of option buyers — respect theta decay aggressively.
  5. Never hold a stale position — flat for 45+ min means your thesis is dead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models.domain import AssetClass

NY = ZoneInfo("America/New_York")

EXPIRATION_CUTOFF = time(15, 30)
EOD_FLATTEN_TIME = time(15, 50)
MIN_HOLD_MINUTES = 5.0
NEAR_EXPIRY_PROFIT_PCT = 0.10
NEAR_EXPIRY_LOSS_PCT = -0.12

STALE_FLAT_MINUTES = 45.0
STALE_FLAT_THRESHOLD = 0.04


@dataclass(frozen=True)
class OptionExitDecision:
    should_exit: bool
    reason: str
    pnl_pct: float
    peak_pnl_pct: float
    dte: int
    held_minutes: float
    current_value: float
    cost_basis: float


def evaluate_option_exit(
    position,
    *,
    opened_at: datetime | None,
    previous_peak_pnl_pct: float | None = None,
    now: datetime | None = None,
) -> OptionExitDecision:
    """Return an exit decision for a long option position.

    P&L is measured against cost basis: a $1.00 option now worth $1.50 = +50%.
    """
    now = _aware(now or datetime.now(timezone.utc))
    symbol = str(getattr(position, "symbol", "") or "")
    asset_class = getattr(position, "asset_class", None)
    qty = abs(int(float(getattr(position, "qty", 0) or 0)))

    if asset_class != AssetClass.OPTION or qty <= 0:
        return _decision(False, "not_long_option", position, 0.0, previous_peak_pnl_pct, 999, 0.0)

    expiration = _expiration_from_occ(symbol)
    dte = _dte(expiration, now) if expiration else 999
    held_minutes = _held_minutes(opened_at, now)
    cost_basis = _cost_basis(position, qty)
    pnl = float(getattr(position, "unrealized_pnl", 0.0) or 0.0)
    pnl_pct = pnl / cost_basis if cost_basis > 0 else 0.0
    peak_pnl_pct = max(float(previous_peak_pnl_pct or pnl_pct), pnl_pct)

    # ── 1. Hard time-based exits (always fire, regardless of P&L) ──────────

    if dte <= 0 and now.astimezone(NY).time() >= EXPIRATION_CUTOFF:
        return _decision(True, "expiration_day_cutoff", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    if now.astimezone(NY).time() >= EOD_FLATTEN_TIME:
        return _decision(True, "eod_flatten", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 2. Min hold — let the trade breathe after fill ─────────────────────

    if held_minutes < MIN_HOLD_MINUTES and not _severe_loss(pnl_pct, dte):
        return _decision(False, "min_hold", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 3. Hard stop-loss (DTE-scaled, wider than before to avoid noise) ───

    stop = _dte_scaled_stop(dte)
    if pnl_pct <= stop:
        return _decision(True, "hard_stop_loss", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 4. Hard profit target (DTE-scaled, high enough to let runners run) ─

    target = _dte_scaled_target(dte)
    if pnl_pct >= target:
        return _decision(True, "profit_target", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 5. Smart dynamic trailing stop ─────────────────────────────────────
    #    Locks in a % of peak gains that scales with profit size.
    #    Small peak = loose trail (don't choke a developing move).
    #    Big peak = tight trail (protect real money).

    trail_activate = _dte_scaled_trail_activate(dte)
    if peak_pnl_pct >= trail_activate:
        trail_floor = _dynamic_trail_floor(peak_pnl_pct, dte)
        if pnl_pct <= trail_floor:
            return _decision(True, "trailing_profit_protect", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 6. Near-expiration urgency (≤1 DTE: gamma risk is extreme) ─────────

    if dte <= 1 and pnl_pct >= NEAR_EXPIRY_PROFIT_PCT:
        return _decision(True, "near_expiration_take_profit", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    if dte <= 1 and pnl_pct <= NEAR_EXPIRY_LOSS_PCT:
        return _decision(True, "near_expiration_cut_loss", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 7. Stale flat position — thesis is dead, free the capital ──────────
    #    If you're flat (±4%) after 45 min, the setup failed to develop.
    #    Day traders don't hold dead positions hoping for a miracle.

    if held_minutes >= STALE_FLAT_MINUTES and abs(pnl_pct) < STALE_FLAT_THRESHOLD:
        return _decision(True, "stale_flat_position", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 8. Theta decay — bleeding positions on short-dated options ─────────

    if dte <= 3 and held_minutes >= 30.0 and pnl_pct < -0.05:
        return _decision(True, "theta_decay_stale_loser", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    if dte <= 5 and held_minutes >= 45.0 and pnl_pct < -0.08:
        return _decision(True, "theta_decay_mid_dte_loser", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    # ── 9. Extended hold bleeding (any DTE) ────────────────────────────────
    #    If held 2+ hours and still losing, the trade is not working.

    if held_minutes >= 120.0 and pnl_pct < -0.10:
        return _decision(True, "extended_hold_loser", position, pnl_pct, peak_pnl_pct, dte, held_minutes)

    return _decision(False, "hold", position, pnl_pct, peak_pnl_pct, dte, held_minutes)


# ── DTE-scaled thresholds ────────────────────────────────────────────────────

def _dte_scaled_target(dte: int) -> float:
    """Hard profit target — only hit on big runners."""
    if dte <= 0:
        return 0.50
    if dte <= 2:
        return 0.45
    if dte <= 5:
        return 0.55
    if dte <= 10:
        return 0.70
    return 0.80


def _dte_scaled_stop(dte: int) -> float:
    """Hard stop — wide enough to survive normal bid-ask noise."""
    if dte <= 0:
        return -0.25
    if dte <= 2:
        return -0.22
    if dte <= 5:
        return -0.25
    if dte <= 10:
        return -0.28
    return -0.30


def _dte_scaled_trail_activate(dte: int) -> float:
    """Trailing stop activation threshold — don't trail until real profit."""
    if dte <= 2:
        return 0.15
    if dte <= 5:
        return 0.18
    return 0.22


def _dynamic_trail_floor(peak_pnl_pct: float, dte: int) -> float:
    """Dynamic trailing floor that scales with profit size.

    Small peaks → loose trail (let the trade develop).
    Large peaks → tight trail (protect real money).

    The trail locks in an increasing % of peak gains:
      peak < 25%  → lock in 45% of peak
      peak 25-50% → lock in 55% of peak
      peak 50-80% → lock in 65% of peak
      peak > 80%  → lock in 72% of peak

    For short DTE (0-2), lock in slightly more because theta risk is higher.
    """
    dte_bonus = 0.05 if dte <= 2 else 0.0

    if peak_pnl_pct < 0.25:
        lock_pct = 0.45 + dte_bonus
    elif peak_pnl_pct < 0.50:
        lock_pct = 0.55 + dte_bonus
    elif peak_pnl_pct < 0.80:
        lock_pct = 0.65 + dte_bonus
    else:
        lock_pct = 0.72 + dte_bonus

    return peak_pnl_pct * lock_pct


def _severe_loss(pnl_pct: float, dte: int) -> bool:
    threshold = _dte_scaled_stop(dte) * 0.85
    return pnl_pct <= threshold


# ── Decision builder ─────────────────────────────────────────────────────────

def _decision(
    should_exit: bool,
    reason: str,
    position,
    pnl_pct: float,
    peak_pnl_pct: float | None,
    dte: int,
    held_minutes: float,
) -> OptionExitDecision:
    qty = abs(int(float(getattr(position, "qty", 0) or 0)))
    cost_basis = _cost_basis(position, qty)
    current_value = _current_value(position, qty)
    return OptionExitDecision(
        should_exit=should_exit,
        reason=reason,
        pnl_pct=round(pnl_pct, 4),
        peak_pnl_pct=round(float(peak_pnl_pct or pnl_pct), 4),
        dte=dte,
        held_minutes=round(held_minutes, 2),
        current_value=round(current_value, 2),
        cost_basis=round(cost_basis, 2),
    )


# ── Position value helpers ───────────────────────────────────────────────────

def _cost_basis(position, qty: int) -> float:
    avg_price = abs(float(getattr(position, "avg_price", 0.0) or 0.0))
    return avg_price * qty * 100


def _current_value(position, qty: int) -> float:
    current_price = abs(float(getattr(position, "current_price", 0.0) or 0.0))
    market_value = abs(float(getattr(position, "market_value", 0.0) or 0.0))
    return market_value or current_price * qty * 100


def _held_minutes(opened_at: datetime | None, now: datetime) -> float:
    if opened_at is None:
        return 9999.0
    opened = _aware(opened_at)
    return max(0.0, (now - opened).total_seconds() / 60.0)


def _dte(expiration: datetime, now: datetime) -> int:
    return (expiration.astimezone(NY).date() - now.astimezone(NY).date()).days


def _expiration_from_occ(symbol: str) -> datetime | None:
    for i in range(1, len(symbol) - 14):
        chunk = symbol[i:i + 6]
        right_idx = i + 6
        strike_start = i + 7
        strike_end = i + 15
        if symbol[right_idx:right_idx + 1] not in {"C", "P"}:
            continue
        if not symbol[strike_start:strike_end].isdigit():
            continue
        try:
            exp = datetime.strptime(chunk, "%y%m%d")
        except ValueError:
            continue
        return exp.replace(tzinfo=NY)
    return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
