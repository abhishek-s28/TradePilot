"""MACD Signal-Line Crossover Strategy.

Rules (bidirectional):
  Bullish:
    - MACD line crosses ABOVE signal line (bullish cross)
    - MACD histogram turns positive
    - Price is above 20-period EMA (trend confirmation)
    - RSI(14) between 40 and 70 (not overbought)

  Bearish (put-only — no short stocks in Phase 1):
    - MACD line crosses BELOW signal line
    - Histogram turns negative
    - Price is below 20-period EMA
    - RSI > 30

Stop: 1.5× ATR
Target: 2× risk (standard R/R)
"""
from __future__ import annotations

import numpy as np

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, ema, macd, rsi


@registry.register
class MACDCrossoverStrategy(Strategy):
    name = "macd_crossover"
    description = "MACD signal-line cross with EMA trend filter and RSI guard."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "ema_period": 20,
        "rsi_period": 14,
        "rsi_min_bull": 35,
        "rsi_max_bull": 75,
        "rsi_max_bear": 65,
        "atr_period": 14,
        "stop_atr_mult": 1.5,
        "rr": 2.0,
        "min_confidence": 0.44,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []

        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]

        macd_df = macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])
        ema20 = ema(close, p["ema_period"])
        rsi_s = rsi(close, p["rsi_period"])
        a = atr(high, low, close, p["atr_period"])

        # We need at least 2 bars to detect a crossover
        if len(macd_df) < 2:
            return []

        # Current and prior bar values
        hist_now = float(macd_df["hist"].iloc[-1])
        hist_prev = float(macd_df["hist"].iloc[-2])
        macd_now = float(macd_df["macd"].iloc[-1])
        sig_now = float(macd_df["signal"].iloc[-1])
        macd_prev = float(macd_df["macd"].iloc[-2])
        sig_prev = float(macd_df["signal"].iloc[-2])

        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_rsi = float(rsi_s.iloc[-1])
        last_atr = float(a.iloc[-1])

        if any(np.isnan(x) for x in (hist_now, hist_prev, last_ema20, last_rsi, last_atr)):
            return []

        signals: list[Signal] = []

        # ── Bullish crossover ──────────────────────────────────────────────
        bull_cross = (macd_prev < sig_prev) and (macd_now > sig_now) and hist_now > 0
        bull_trend = last_close > last_ema20
        bull_rsi = p["rsi_min_bull"] <= last_rsi <= p["rsi_max_bull"]

        if bull_cross and bull_trend and bull_rsi:
            stop = round(last_close - p["stop_atr_mult"] * last_atr, 2)
            risk = last_close - stop
            if risk > 0:
                target = round(last_close + p["rr"] * risk, 2)

                # Confidence: stronger when histogram is clearly positive, RSI mid-range
                hist_score = min(1.0, hist_now / (last_atr * 0.1 + 1e-9))
                rsi_score = 1.0 - abs(last_rsi - 55) / 15
                confidence = round(min(1.0, max(0.0, 0.5 * hist_score + 0.5 * rsi_score)), 3)

                regime_adj = 0.1 if ctx.market_regime == "bullish" else (
                    -0.15 if ctx.market_regime == "bearish" else 0.0
                )
                confidence = round(max(0.0, min(1.0, confidence + regime_adj)), 3)

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
                            f"MACD bullish cross above signal line. Hist {hist_now:.4f} > 0. "
                            f"Price {last_close:.2f} above EMA20 {last_ema20:.2f}. RSI {last_rsi:.1f}."
                        ),
                        invalidation=f"MACD histogram turns negative or close below {stop:.2f}.",
                        risk_reward=p["rr"],
                        suggested_qty=1,
                        suitable_for_options=True,
                        holding_period_hint="intraday-to-swing",
                        generated_at=ctx.now,
                        status=SignalStatus.NEW,
                        metadata={
                            "macd_hist": round(hist_now, 4),
                            "rsi": round(last_rsi, 1),
                            "ema20": round(last_ema20, 2),
                            "atr": round(last_atr, 2),
                            "regime": ctx.market_regime,
                        },
                    ))

        # ── Bearish crossover (options put / skip if no options) ──────────
        bear_cross = (macd_prev > sig_prev) and (macd_now < sig_now) and hist_now < 0
        bear_trend = last_close < last_ema20
        bear_rsi = last_rsi > 30 and last_rsi < p["rsi_max_bear"]

        if bear_cross and bear_trend and bear_rsi and ctx.market_regime in ("bearish", "choppy"):
            stop = round(last_close + p["stop_atr_mult"] * last_atr, 2)
            risk = stop - last_close
            if risk > 0:
                target = round(last_close - p["rr"] * risk, 2)

                hist_score = min(1.0, abs(hist_now) / (last_atr * 0.1 + 1e-9))
                rsi_score = 1.0 - abs(last_rsi - 45) / 15
                confidence = round(min(1.0, max(0.0, 0.5 * hist_score + 0.5 * rsi_score)), 3)
                confidence = max(0.0, min(1.0, confidence - 0.1))  # bearish discount

                if confidence >= p["min_confidence"]:
                    signals.append(Signal(
                        strategy=self.name,
                        asset_class=AssetClass.OPTION,
                        symbol=ctx.symbol,    # underlying — scanner will resolve to put
                        underlying=ctx.symbol,
                        direction=Direction.BEARISH,
                        entry=round(last_close, 2),
                        stop_loss=stop,
                        take_profit=target,
                        confidence=confidence,
                        reason=(
                            f"MACD bearish cross below signal. Hist {hist_now:.4f} < 0. "
                            f"Price {last_close:.2f} below EMA20 {last_ema20:.2f}. RSI {last_rsi:.1f}."
                        ),
                        invalidation=f"MACD histogram turns positive or close above {stop:.2f}.",
                        risk_reward=p["rr"],
                        suggested_qty=1,
                        suitable_for_options=True,
                        holding_period_hint="intraday",
                        generated_at=ctx.now,
                        status=SignalStatus.NEW,
                        metadata={
                            "macd_hist": round(hist_now, 4),
                            "rsi": round(last_rsi, 1),
                            "regime": ctx.market_regime,
                        },
                    ))

        return signals
