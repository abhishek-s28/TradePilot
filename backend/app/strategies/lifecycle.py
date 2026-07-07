"""Always-on lifecycle strategies.

These strategies are intentionally simple and liquid-instrument focused.  They
give the auto-trader something sensible to evaluate in every tradable session:
intraday momentum for active names and slower ETF accumulation for core holds.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from app.models.domain import AssetClass, Direction, Signal
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, ema, relative_volume, rsi


def _price(ctx: StrategyContext, fallback: float) -> float:
    quote = ctx.latest_quote
    if quote is not None and getattr(quote, "mid", 0) > 0:
        return float(quote.mid)
    return float(fallback)


@registry.register
class SessionMomentumStrategy(Strategy):
    name = "session_momentum"
    description = "Session-aware equity momentum for regular, pre-market, after-hours, and overnight scans."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "fast_ema": 8,
        "slow_ema": 21,
        "breakout_lookback": 12,
        "min_regular_rel_volume": 0.9,
        "min_extended_rel_volume": 0.6,
        "atr_stop_mult": 1.25,
        "reward_mult": 2.2,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        p = self.params
        if df is None or len(df) < max(60, int(p["slow_ema"]) + 5):
            return []

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)

        fast = ema(close, int(p["fast_ema"]))
        slow = ema(close, int(p["slow_ema"]))
        rv = relative_volume(volume, 20)
        a = atr(high, low, close, 14)
        if np.isnan([fast.iloc[-1], slow.iloc[-1], rv.iloc[-1], a.iloc[-1]]).any():
            return []

        session = str(ctx.extra.get("market_session", "regular"))
        min_rv = (
            float(p["min_regular_rel_volume"])
            if session == "regular"
            else float(p["min_extended_rel_volume"])
        )
        lookback = int(p["breakout_lookback"])
        recent_high = float(high.iloc[-lookback - 1:-1].max())
        latest = float(close.iloc[-1])
        rel_vol = float(rv.iloc[-1])
        trend_strength = (float(fast.iloc[-1]) - float(slow.iloc[-1])) / max(latest, 1)

        if not (
            latest >= recent_high * 0.998
            and fast.iloc[-1] > slow.iloc[-1]
            and rel_vol >= min_rv
            and trend_strength > 0
        ):
            return []

        entry = round(_price(ctx, latest), 2)
        stop_distance = max(float(a.iloc[-1]) * float(p["atr_stop_mult"]), entry * 0.006)
        stop = round(max(0.01, entry - stop_distance), 2)
        target = round(entry + stop_distance * float(p["reward_mult"]), 2)
        rr = round((target - entry) / max(entry - stop, 0.01), 2)
        confidence = min(
            0.92,
            0.56
            + min(0.18, rel_vol / 12)
            + min(0.12, trend_strength * 20)
            + (0.04 if session == "regular" else 0.0),
        )

        return [
            Signal(
                strategy=self.name,
                asset_class=AssetClass.STOCK,
                symbol=ctx.symbol,
                direction=Direction.BULLISH,
                entry=entry,
                stop_loss=stop,
                take_profit=target,
                confidence=round(confidence, 3),
                reason=(
                    f"{session} momentum: close {latest:.2f} pressing {lookback}-bar high "
                    f"{recent_high:.2f}, EMA{p['fast_ema']} > EMA{p['slow_ema']}, "
                    f"relative volume {rel_vol:.2f}x."
                ),
                invalidation=f"Close below {stop:.2f} or momentum loses EMA stack.",
                risk_reward=rr,
                suggested_qty=1,
                suitable_for_options=True,
                holding_period_hint="intraday",
                generated_at=ctx.now or datetime.now(timezone.utc),
                metadata={
                    "market_session": session,
                    "rel_volume": round(rel_vol, 2),
                    "trend_strength": round(trend_strength, 4),
                },
            )
        ]


@registry.register
class LongTermETFAllocatorStrategy(Strategy):
    name = "long_term_etf_allocator"
    description = "Core ETF accumulation when broad ETFs are in an orderly uptrend."
    timeframe = "5Min"
    lookback_bars = 240
    default_params = {
        "fast_ema": 34,
        "slow_ema": 144,
        "max_rsi": 72,
        "min_rsi": 45,
        "atr_stop_mult": 3.0,
        "reward_mult": 4.0,
    }

    _ETF_UNIVERSE = {
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "SCHD", "XLK", "XLF", "XLV",
        "XLE", "XLI", "TQQQ", "SOXL", "ARKK",
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        if ctx.symbol.upper() not in self._ETF_UNIVERSE:
            return []

        df = ctx.bars
        p = self.params
        need = max(int(p["slow_ema"]) + 5, 180)
        if df is None or len(df) < need:
            return []

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        fast = ema(close, int(p["fast_ema"]))
        slow = ema(close, int(p["slow_ema"]))
        momentum_rsi = rsi(close, 14)
        a = atr(high, low, close, 21)
        rsi_val = float(momentum_rsi.iloc[-1])
        if np.isnan(rsi_val):
            rsi_val = 70.0 if float(close.iloc[-1]) >= float(close.iloc[-15]) else 50.0
        vals = [fast.iloc[-1], slow.iloc[-1], a.iloc[-1]]
        if np.isnan(vals).any():
            return []

        latest = float(close.iloc[-1])
        trend_ok = latest > float(fast.iloc[-1]) > float(slow.iloc[-1])
        orderly = float(p["min_rsi"]) <= rsi_val <= float(p["max_rsi"])
        if not (trend_ok and orderly and ctx.market_regime in {"bullish", "choppy", "unknown"}):
            return []

        entry = round(_price(ctx, latest), 2)
        stop_distance = max(float(a.iloc[-1]) * float(p["atr_stop_mult"]), entry * 0.025)
        stop = round(max(0.01, entry - stop_distance), 2)
        target = round(entry + stop_distance * float(p["reward_mult"]), 2)
        rr = round((target - entry) / max(entry - stop, 0.01), 2)
        confidence = min(0.88, 0.62 + min(0.18, (latest / float(slow.iloc[-1]) - 1.0) * 2))

        return [
            Signal(
                strategy=self.name,
                asset_class=AssetClass.STOCK,
                symbol=ctx.symbol,
                direction=Direction.BULLISH,
                entry=entry,
                stop_loss=stop,
                take_profit=target,
                confidence=round(confidence, 3),
                reason=(
                    f"Core ETF trend: price {latest:.2f} above EMA{p['fast_ema']} "
                    f"and EMA{p['slow_ema']} with RSI {rsi_val:.1f}."
                ),
                invalidation=f"Trend thesis invalid below {stop:.2f}.",
                risk_reward=rr,
                suggested_qty=1,
                suitable_for_options=False,
                holding_period_hint="swing",
                generated_at=ctx.now or datetime.now(timezone.utc),
                metadata={
                    "market_session": str(ctx.extra.get("market_session", "regular")),
                    "rsi": round(rsi_val, 1),
                    "ema_fast": round(float(fast.iloc[-1]), 2),
                    "ema_slow": round(float(slow.iloc[-1]), 2),
                },
            )
        ]
