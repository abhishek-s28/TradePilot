"""Momentum Breakout Strategy.

Rules (long):
 - Last close above 20-bar high → breakout
 - Volume ≥ 1.5× 20-bar average (relative volume)
 - Price above VWAP
 - RSI(14) between 50 and 75 (momentum but not overheated)

Stop: most recent swing low (or ATR-based).
Target: 2× risk.
"""
from __future__ import annotations

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, relative_volume, rsi, vwap


@registry.register
class MomentumBreakoutStrategy(Strategy):
    name = "momentum_breakout"
    description = "Volume-confirmed breakout above N-bar high, above VWAP, healthy RSI."
    timeframe = "5Min"
    lookback_bars = 100
    default_params = {
        "breakout_lookback": 20,
        "min_rel_volume": 1.2,    # Lowered from 1.5
        "rsi_min": 45,            # Widened from 50
        "rsi_max": 78,            # Widened from 75
        "atr_period": 14,
        "stop_atr_mult": 1.5,
        "rr": 2.0,
        "min_confidence": 0.48,   # Lowered from 0.55
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []

        p = self.params
        n = p["breakout_lookback"]
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        prior_high = high.iloc[-(n + 1):-1].max()
        last_close = close.iloc[-1]
        last_rv = relative_volume(vol, lookback=n).iloc[-1]
        last_rsi = rsi(close, 14).iloc[-1]
        v = vwap(df).iloc[-1]
        a = atr(high, low, close, p["atr_period"]).iloc[-1]

        if any(x != x for x in (prior_high, last_rv, last_rsi, v, a)):  # NaN check
            return []

        # Bullish conditions
        breakout = last_close > prior_high
        good_vol = last_rv >= p["min_rel_volume"]
        above_vwap = last_close > v
        ok_rsi = p["rsi_min"] <= last_rsi <= p["rsi_max"]

        if not (breakout and good_vol and above_vwap and ok_rsi):
            return []

        stop = round(last_close - p["stop_atr_mult"] * a, 2)
        risk = last_close - stop
        if risk <= 0:
            return []
        target = round(last_close + p["rr"] * risk, 2)

        # Confidence: higher when rel-volume strong & RSI mid-range
        rv_score = min(1.0, (last_rv - p["min_rel_volume"]) / 2.0 + 0.5)
        rsi_score = 1.0 - abs(last_rsi - 62.5) / 12.5  # peak at 62.5
        confidence = round(max(0.0, min(1.0, 0.5 * rv_score + 0.5 * rsi_score)), 3)

        if confidence < p["min_confidence"]:
            return []

        regime_bonus = 0.1 if ctx.market_regime == "bullish" else (
            -0.15 if ctx.market_regime == "bearish" else 0.0
        )
        confidence = max(0.0, min(1.0, confidence + regime_bonus))

        return [
            Signal(
                strategy=self.name,
                asset_class=AssetClass.STOCK,
                symbol=ctx.symbol,
                direction=Direction.BULLISH,
                entry=round(float(last_close), 2),
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                reason=(
                    f"Close {last_close:.2f} broke {n}-bar high {prior_high:.2f} on "
                    f"{last_rv:.2f}× rel volume, above VWAP {v:.2f}, RSI {last_rsi:.1f}."
                ),
                invalidation=f"Close below {stop} invalidates breakout.",
                risk_reward=p["rr"],
                suggested_qty=1,
                suitable_for_options=True,
                holding_period_hint="intraday-to-swing",
                generated_at=ctx.now,
                status=SignalStatus.NEW,
                metadata={
                    "rel_volume": float(last_rv),
                    "rsi": float(last_rsi),
                    "atr": float(a),
                    "regime": ctx.market_regime,
                },
            )
        ]
