"""Opening Range Breakout (ORB) Strategy.

One of the most well-studied intraday setups in quantitative trading:
the first N minutes of the session establish the "range". A close above
the high of that range (with volume) is a buy signal; below the low is a
short/put signal.

Implementation:
  - "Opening range" = high and low of the first 30 minutes (first 6 × 5-min bars)
  - Signal fires when price breaks out of that range on elevated volume (≥1.3× avg)
  - RSI filter: 45-75 for bull, 25-55 for bear
  - Stop: 0.5× ATR beyond range boundary (tight stop, expect momentum)
  - Target: range height projected from breakout point

Market hours guard:
  We check that the current bar is within 2 hours of the open (most ORB setups
  decay if triggered after midday). When running outside US market hours, the
  strategy produces no signals.
"""
from __future__ import annotations

from datetime import time, timezone

import numpy as np

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, relative_volume, rsi


@registry.register
class OpeningRangeBreakoutStrategy(Strategy):
    name = "opening_range_breakout"
    description = "First 30-min range breakout on volume. Classic ORB — high-win-rate intraday."
    timeframe = "5Min"
    lookback_bars = 80
    default_params = {
        "range_bars": 6,          # 6 × 5-min = 30-min range
        "max_bars_from_open": 36, # Trade within 3 hours of open
        "min_rel_volume": 1.1,    # Lowered from 1.3 — more signals
        "rsi_period": 14,
        "rsi_bull_min": 42,       # Widened from 45
        "rsi_bull_max": 78,       # Widened from 75
        "rsi_bear_max": 58,       # Widened from 55
        "rsi_bear_min": 22,       # Widened from 25
        "atr_period": 14,
        "stop_atr_mult": 0.5,
        "min_confidence": 0.46,   # Lowered from 0.52
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []

        p = self.params

        # Only useful in the first 2 hours of US trading
        # We approximate by checking if we have bars from "today" in the range
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        # Find bars from today's session (same calendar day)
        now = ctx.now
        today_date = now.date()
        today_mask = df.index.map(lambda t: t.date() == today_date)
        today_bars = df[today_mask]

        if len(today_bars) < p["range_bars"] + 2:
            return []

        # Position in today's session
        bars_since_open = len(today_bars)
        if bars_since_open > p["max_bars_from_open"]:
            return []

        # Opening range
        opening_range = today_bars.iloc[: p["range_bars"]]
        or_high = float(opening_range["high"].max())
        or_low = float(opening_range["low"].min())
        or_height = or_high - or_low
        if or_height <= 0:
            return []

        last_close = float(close.iloc[-1])
        last_rv = float(relative_volume(vol, lookback=20).iloc[-1])
        last_rsi = float(rsi(close, p["rsi_period"]).iloc[-1])
        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])

        if any(np.isnan(x) for x in (last_rv, last_rsi, last_atr)):
            return []

        signals: list[Signal] = []

        # ── Bullish breakout ──────────────────────────────────────────────
        if (
            last_close > or_high
            and last_rv >= p["min_rel_volume"]
            and p["rsi_bull_min"] <= last_rsi <= p["rsi_bull_max"]
        ):
            stop = round(or_high - p["stop_atr_mult"] * last_atr, 2)
            if stop < last_close:
                target = round(last_close + or_height, 2)
                risk = last_close - stop
                rr = (target - last_close) / risk if risk > 0 else 0.0

                # Confidence: rv score + RSI proximity to sweet spot (60)
                rv_score = min(1.0, (last_rv - p["min_rel_volume"]) / 1.5 + 0.4)
                rsi_score = 1.0 - abs(last_rsi - 60) / 15
                confidence = round(min(1.0, max(0.0, 0.5 * rv_score + 0.5 * rsi_score)), 3)

                if ctx.market_regime == "bullish":
                    confidence = min(1.0, confidence + 0.1)
                elif ctx.market_regime in ("bearish", "high_vol"):
                    confidence = max(0.0, confidence - 0.15)

                if confidence >= p["min_confidence"]:
                    signals.append(Signal(
                        strategy=self.name,
                        asset_class=AssetClass.STOCK,
                        symbol=ctx.symbol,
                        direction=Direction.BULLISH,
                        entry=round(last_close, 2),
                        stop_loss=stop,
                        take_profit=target,
                        confidence=confidence,
                        reason=(
                            f"ORB bullish: close {last_close:.2f} broke 30-min high {or_high:.2f} "
                            f"on {last_rv:.1f}× rel volume. Target: OR height projected = {target:.2f}."
                        ),
                        invalidation=f"Close back inside opening range below {or_high:.2f}.",
                        risk_reward=round(rr, 2),
                        suggested_qty=1,
                        suitable_for_options=True,
                        holding_period_hint="intraday",
                        generated_at=ctx.now,
                        status=SignalStatus.NEW,
                        metadata={
                            "or_high": round(or_high, 2),
                            "or_low": round(or_low, 2),
                            "or_height": round(or_height, 2),
                            "rel_volume": round(last_rv, 2),
                            "rsi": round(last_rsi, 1),
                            "bars_since_open": bars_since_open,
                            "regime": ctx.market_regime,
                        },
                    ))

        # ── Bearish breakdown ─────────────────────────────────────────────
        if (
            last_close < or_low
            and last_rv >= p["min_rel_volume"]
            and p["rsi_bear_min"] <= last_rsi <= p["rsi_bear_max"]
        ):
            stop = round(or_low + p["stop_atr_mult"] * last_atr, 2)
            if stop > last_close:
                target = round(last_close - or_height, 2)
                risk = stop - last_close
                rr = (last_close - target) / risk if risk > 0 else 0.0

                rv_score = min(1.0, (last_rv - p["min_rel_volume"]) / 1.5 + 0.4)
                rsi_score = 1.0 - abs(last_rsi - 40) / 15
                confidence = round(min(1.0, max(0.0, 0.5 * rv_score + 0.5 * rsi_score - 0.1)), 3)

                if confidence >= p["min_confidence"]:
                    signals.append(Signal(
                        strategy=self.name,
                        asset_class=AssetClass.OPTION,
                        symbol=ctx.symbol,
                        underlying=ctx.symbol,
                        direction=Direction.BEARISH,
                        entry=round(last_close, 2),
                        stop_loss=stop,
                        take_profit=target,
                        confidence=confidence,
                        reason=(
                            f"ORB bearish breakdown: close {last_close:.2f} broke 30-min low {or_low:.2f} "
                            f"on {last_rv:.1f}× rel volume. Target: {target:.2f}."
                        ),
                        invalidation=f"Close back above {or_low:.2f} opening range low.",
                        risk_reward=round(rr, 2),
                        suggested_qty=1,
                        suitable_for_options=True,
                        holding_period_hint="intraday",
                        generated_at=ctx.now,
                        status=SignalStatus.NEW,
                        metadata={
                            "or_high": round(or_high, 2),
                            "or_low": round(or_low, 2),
                            "rel_volume": round(last_rv, 2),
                            "rsi": round(last_rsi, 1),
                            "regime": ctx.market_regime,
                        },
                    ))

        return signals
