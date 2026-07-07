"""Mean Reversion Strategy.

Rules (long-only baseline):
 - Price closes BELOW lower Bollinger Band(20, 2)
 - RSI(14) < 30
 - Bar is not making a new 20-bar low (capitulation filter)

Stop: 1.5× ATR below entry.
Target: middle Bollinger band.
"""
from __future__ import annotations

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, bollinger, rsi


@registry.register
class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    description = "RSI<30 + close below lower Bollinger band, mean-revert to mid band."
    timeframe = "5Min"
    lookback_bars = 100
    default_params = {
        "bb_period": 20,
        "bb_k": 2.0,
        "rsi_period": 14,
        "rsi_threshold": 30,
        "atr_period": 14,
        "stop_atr_mult": 1.5,
        "min_confidence": 0.44,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        bb = bollinger(df["close"], p["bb_period"], p["bb_k"])
        rsi_series = rsi(df["close"], p["rsi_period"])
        a = atr(df["high"], df["low"], df["close"], p["atr_period"]).iloc[-1]

        last_close = df["close"].iloc[-1]
        last_low = df["low"].iloc[-1]
        prior_lows = df["low"].iloc[-(p["bb_period"] + 1):-1]
        last_rsi = rsi_series.iloc[-1]
        lower = bb["lower"].iloc[-1]
        mid = bb["mid"].iloc[-1]

        if any(x != x for x in (lower, mid, last_rsi, a)):
            return []
        if last_close > lower:
            return []
        if last_rsi > p["rsi_threshold"]:
            return []
        if last_low <= prior_lows.min():
            # making fresh low → don't catch knife
            return []

        stop = round(last_close - p["stop_atr_mult"] * a, 2)
        if stop >= last_close:
            return []
        target = round(float(mid), 2)
        risk = last_close - stop
        reward = target - last_close
        if reward <= 0:
            return []
        rr = reward / risk

        confidence = round(
            min(1.0, 0.4 + (p["rsi_threshold"] - last_rsi) / 30 + min(rr, 2.0) / 5),
            3,
        )
        if confidence < p["min_confidence"]:
            return []

        if ctx.market_regime == "bearish":
            confidence = max(0.0, confidence - 0.15)
        if confidence < p["min_confidence"]:
            return []

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
                    f"Oversold: RSI {last_rsi:.1f} < {p['rsi_threshold']}, close "
                    f"{last_close:.2f} below lower BB {lower:.2f}. Target mid BB {mid:.2f}."
                ),
                invalidation=f"Close below {stop} invalidates setup.",
                risk_reward=round(rr, 2),
                suggested_qty=1,
                suitable_for_options=False,
                holding_period_hint="intraday",
                generated_at=ctx.now,
                status=SignalStatus.NEW,
                metadata={"rsi": float(last_rsi), "atr": float(a)},
            )
        ]
