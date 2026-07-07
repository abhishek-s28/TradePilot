from __future__ import annotations

import numpy as np

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, bollinger, rsi


@registry.register
class GammaScalpingStrategy(Strategy):
    """Bollinger Band compression identifies low-IV environments where a long
    straddle is cheap relative to its historical cost. Entry requires price
    to sit within 0.5 % of a $5-interval round number (natural options strike),
    ensuring a single at-the-money contract can express both legs cleanly.
    RSI 45–55 confirms directional neutrality — no trend bias means neither leg
    is immediately losing theta to delta.

    Two signals are emitted per trigger: a CALL leg (bullish) and a PUT leg
    (bearish). The signal service resolves each to a real OCC contract. The
    trader buys both to form the straddle; profit when the underlying moves
    more than the combined premium paid.

    Confidence formula:
        compression_score ∈ [0,1]: how far below the Nth-percentile threshold
            the current width sits — deeper compression → more explosive release.
        proximity_score ∈ [0,1]: inverse of distance to round number —
            tighter pin → cheaper, purer ATM straddle.
        rsi_neutral_score ∈ [0,1]: 1.0 at RSI=50, decays as |RSI − 50| grows —
            strong directional momentum defeats the neutral thesis.
    """

    name = "gamma_scalping"
    description = (
        "BB compression near ATM strike + RSI neutral → long straddle "
        "on imminent volatility expansion."
    )
    timeframe = "5Min"
    lookback_bars = 130
    default_params = {
        "bb_period": 20,
        "bb_k": 2.0,
        "compression_window": 100,
        "compression_percentile": 0.10,
        "round_interval": 5.0,
        "round_tolerance_pct": 0.005,
        "rsi_period": 14,
        "rsi_min": 45,
        "rsi_max": 55,
        "atr_period": 14,
        "stop_premium_pct": 0.40,
        "target_premium_pct": 1.20,
        "min_rr": 1.5,
        "min_confidence": 0.48,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low = df["close"], df["high"], df["low"]

        bb = bollinger(close, p["bb_period"], p["bb_k"])
        width = (bb["upper"] - bb["lower"]) / bb["mid"].replace(0, np.nan)

        window = min(p["compression_window"], len(width) - 1)
        threshold = float(width.iloc[-window:].quantile(p["compression_percentile"]))
        current_width = float(width.iloc[-1])
        if np.isnan(current_width) or np.isnan(threshold) or current_width > threshold:
            return []

        last_close = float(close.iloc[-1])
        nearest_round = round(last_close / p["round_interval"]) * p["round_interval"]
        distance_pct = abs(last_close - nearest_round) / max(last_close, 1.0)
        if distance_pct > p["round_tolerance_pct"]:
            return []

        rsi_val = float(rsi(close, p["rsi_period"]).iloc[-1])
        if np.isnan(rsi_val) or not (p["rsi_min"] <= rsi_val <= p["rsi_max"]):
            return []

        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])
        if np.isnan(last_atr) or last_atr <= 0:
            return []

        # Estimate combined straddle cost from BB width (width ≈ 4σ of daily move;
        # ATM straddle ≈ 0.8σ per Brenner-Subrahmanyam approximation).
        estimated_premium = round(max(0.10, last_close * current_width * 0.20), 2)
        stop_px = round(max(0.01, estimated_premium * (1.0 - p["stop_premium_pct"])), 2)
        target_px = round(estimated_premium * (1.0 + p["target_premium_pct"]), 2)
        rr = (target_px - estimated_premium) / max(estimated_premium - stop_px, 0.01)
        if rr < p["min_rr"]:
            return []

        compression_rank = float(width.iloc[-window:].rank(pct=True).iloc[-1])
        compression_score = max(0.0, 1.0 - compression_rank / p["compression_percentile"])
        proximity_score = max(0.0, 1.0 - distance_pct / p["round_tolerance_pct"])
        rsi_neutral_score = max(0.0, 1.0 - abs(rsi_val - 50.0) / 5.0)
        confidence = round(
            min(1.0, max(0.0,
                0.48 + 0.20 * compression_score
                     + 0.15 * proximity_score
                     + 0.15 * rsi_neutral_score)),
            3,
        )
        if confidence < p["min_confidence"]:
            return []

        shared_meta = {
            "straddle_setup": True,
            "bb_width": round(current_width, 4),
            "bb_width_threshold": round(threshold, 4),
            "round_strike": nearest_round,
            "proximity_pct": round(distance_pct * 100, 3),
            "rsi": round(rsi_val, 1),
            "estimated_straddle_premium": estimated_premium,
            "options_expression": "long_straddle",
        }
        base_reason = (
            f"Gamma scalp: BB width {current_width:.4f} below "
            f"{p['compression_percentile']:.0%}-tile threshold {threshold:.4f}. "
            f"Price ${last_close:.2f} within {distance_pct * 100:.2f}% of ${nearest_round:.0f} ATM strike. "
            f"RSI {rsi_val:.1f} (neutral). Straddle cost ≈${estimated_premium:.2f}."
        )
        # Call leg — underlying stop below entry is valid for option resolution
        call_stop = round(last_close - p["stop_premium_pct"] * last_atr, 2)
        call_target = round(last_close + p["target_premium_pct"] * last_atr * 2, 2)
        # Put leg — symmetric; _resolve_option_contract replaces these with premium levels
        put_stop = round(last_close - p["stop_premium_pct"] * last_atr, 2)
        put_target = round(last_close - p["target_premium_pct"] * last_atr * 2, 2)

        call = Signal(
            strategy=self.name,
            asset_class=AssetClass.OPTION,
            symbol=ctx.symbol,
            underlying=ctx.symbol,
            direction=Direction.BULLISH,
            entry=round(last_close, 2),
            stop_loss=min(call_stop, last_close - 0.01),
            take_profit=call_target,
            confidence=confidence,
            reason=f"{base_reason} CALL leg.",
            invalidation=(
                f"Price stays within ±{p['stop_premium_pct']*100:.0f}% of ${nearest_round:.0f} "
                f"through expiry (theta decay destroys premium)."
            ),
            risk_reward=round(rr, 2),
            suggested_qty=1,
            suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now,
            status=SignalStatus.NEW,
            metadata={**shared_meta, "straddle_leg": "call"},
        )
        put = Signal(
            strategy=self.name,
            asset_class=AssetClass.OPTION,
            symbol=ctx.symbol,
            underlying=ctx.symbol,
            direction=Direction.BEARISH,
            entry=round(last_close, 2),
            stop_loss=min(put_stop, last_close - 0.01),
            take_profit=round(last_close - 0.01, 2),
            confidence=confidence,
            reason=f"{base_reason} PUT leg.",
            invalidation=(
                f"Price stays within ±{p['stop_premium_pct']*100:.0f}% of ${nearest_round:.0f}."
            ),
            risk_reward=round(rr, 2),
            suggested_qty=1,
            suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now,
            status=SignalStatus.NEW,
            metadata={**shared_meta, "straddle_leg": "put"},
        )
        return [call, put]

    def validate(self) -> list[str]:
        issues = []
        p = self.params
        if not (0 < p["compression_percentile"] < 0.5):
            issues.append("compression_percentile must be in (0, 0.5)")
        if not (0 < p["round_tolerance_pct"] < 0.05):
            issues.append("round_tolerance_pct must be in (0, 0.05)")
        if p["stop_premium_pct"] >= p["target_premium_pct"]:
            issues.append("target_premium_pct must exceed stop_premium_pct")
        return issues
