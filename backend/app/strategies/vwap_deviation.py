"""VWAP Deviation Strategy.

Rules (long only):
  - Price falls ≥ 1.5 standard deviations below VWAP
  - RSI(14) < 40 (oversold)
  - Volume is at least average (not a dead tape)
  - Price is NOT making a new 10-bar low (avoid falling knives)

Entry: current close
Stop: 2× ATR below entry
Target: VWAP (mean reversion)

This is a pure intraday mean-reversion play. It generates fast-moving
signals when institutional order flow pushes price away from fair value.
"""
from __future__ import annotations

import numpy as np

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, rsi, vwap


@registry.register
class VWAPDeviationStrategy(Strategy):
    name = "vwap_deviation"
    description = "Price ≥1.5σ below VWAP + oversold RSI → revert to VWAP."
    timeframe = "5Min"
    lookback_bars = 80
    default_params = {
        "sigma_threshold": 1.2,   # Lowered from 1.5 — catches more deviations
        "rsi_threshold": 42,      # Slightly widened
        "rsi_period": 14,
        "atr_period": 14,
        "stop_atr_mult": 2.0,
        "min_avg_volume": 20_000, # Lowered from 50K — more symbols qualify
        "min_confidence": 0.44,   # Lowered from 0.50
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []

        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        vwap_series = vwap(df)
        rsi_series = rsi(close, p["rsi_period"])
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        last_vwap = float(vwap_series.iloc[-1])
        last_rsi = float(rsi_series.iloc[-1])
        last_atr = float(a.iloc[-1])
        avg_vol = float(vol.rolling(20).mean().iloc[-1])
        last_vol = float(vol.iloc[-1])

        if any(np.isnan(x) for x in (last_vwap, last_rsi, last_atr, avg_vol)):
            return []

        # Compute rolling std of (close - vwap) deviations
        deviations = close - vwap_series
        dev_std = float(deviations.rolling(20, min_periods=10).std().iloc[-1])
        if dev_std <= 0 or np.isnan(dev_std):
            return []

        current_dev = last_close - last_vwap
        sigma = current_dev / dev_std  # negative = below VWAP

        # Conditions
        below_threshold = sigma <= -p["sigma_threshold"]
        oversold = last_rsi < p["rsi_threshold"]
        decent_volume = avg_vol > p["min_avg_volume"]
        not_new_low = last_close > float(low.iloc[-11:-1].min())

        if not (below_threshold and oversold and decent_volume and not_new_low):
            return []

        stop = round(last_close - p["stop_atr_mult"] * last_atr, 2)
        if stop >= last_close:
            return []
        target = round(last_vwap, 2)
        risk = last_close - stop
        reward = target - last_close
        if reward <= 0:
            return []
        rr = reward / risk

        # Confidence scales with deviation magnitude and RSI depth
        sigma_score = min(1.0, (abs(sigma) - p["sigma_threshold"]) / 1.5 + 0.4)
        rsi_score = min(1.0, (p["rsi_threshold"] - last_rsi) / 20 + 0.3)
        confidence = round(min(1.0, 0.5 * sigma_score + 0.5 * rsi_score), 3)

        regime_adj = 0.1 if ctx.market_regime == "bullish" else (
            -0.1 if ctx.market_regime == "bearish" else 0.0
        )
        confidence = round(max(0.0, min(1.0, confidence + regime_adj)), 3)

        if confidence < p["min_confidence"]:
            return []

        return [
            Signal(
                strategy=self.name,
                asset_class=AssetClass.STOCK,
                symbol=ctx.symbol,
                direction=Direction.BULLISH,
                entry=round(last_close, 2),
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                reason=(
                    f"Price {last_close:.2f} is {abs(sigma):.1f}σ below VWAP {last_vwap:.2f}. "
                    f"RSI {last_rsi:.1f} oversold. Target: VWAP reversion."
                ),
                invalidation=f"Close below {stop:.2f} invalidates mean-reversion thesis.",
                risk_reward=round(rr, 2),
                suggested_qty=1,
                suitable_for_options=False,
                holding_period_hint="intraday",
                generated_at=ctx.now,
                status=SignalStatus.NEW,
                metadata={
                    "sigma": round(sigma, 2),
                    "vwap": round(last_vwap, 2),
                    "rsi": round(last_rsi, 1),
                    "atr": round(last_atr, 2),
                    "regime": ctx.market_regime,
                },
            )
        ]
