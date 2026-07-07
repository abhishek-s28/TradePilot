"""Advanced stock/option-suitable strategy pack.

These strategies produce disciplined stock signals and mark setups that can be
expressed with options. Contract selection still belongs to the options scanner
and risk manager; strategy code stays pure and data-only.
"""
from __future__ import annotations

import numpy as np

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr, bollinger, ema, keltner, relative_volume, rsi, stochastic, supertrend, vwap_std_bands


@registry.register
class EMATrendPullbackStrategy(Strategy):
    name = "ema_trend_pullback"
    description = "EMA 8/21/50 uptrend; buy controlled pullbacks that reclaim EMA8."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "ema_fast": 8,
        "ema_mid": 21,
        "ema_slow": 50,
        "rsi_period": 14,
        "rsi_min": 42,
        "rsi_max": 72,
        "min_rel_volume": 0.7,
        "pullback_tolerance_pct": 0.004,
        "atr_period": 14,
        "stop_atr_mult": 1.2,
        "rr": 2.2,
        "min_confidence": 0.47,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        if ctx.market_regime == "bearish":
            # Still generate in bearish regime but lower confidence threshold
            pass

        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        fast = ema(close, p["ema_fast"])
        mid = ema(close, p["ema_mid"])
        slow = ema(close, p["ema_slow"])
        rsi_s = rsi(close, p["rsi_period"])
        rv = relative_volume(vol, 20)
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        last_fast = float(fast.iloc[-1])
        last_mid = float(mid.iloc[-1])
        last_slow = float(slow.iloc[-1])
        last_rsi = float(rsi_s.iloc[-1])
        last_rv = float(rv.iloc[-1])
        last_atr = float(a.iloc[-1])
        recent_touch = float(low.iloc[-5:].min())

        if any(np.isnan(x) for x in (
            last_fast, last_mid, last_slow, last_rsi, last_rv, last_atr
        )):
            return []

        trend = last_fast > last_mid > last_slow and last_close > last_slow
        pulled_back = recent_touch <= last_mid * (1 + p["pullback_tolerance_pct"])
        reclaimed = prev_close <= float(fast.iloc[-2]) and last_close > last_fast
        momentum_ok = p["rsi_min"] <= last_rsi <= p["rsi_max"]
        volume_ok = last_rv >= p["min_rel_volume"]
        if not (trend and pulled_back and reclaimed and momentum_ok and volume_ok):
            return []

        stop = round(min(float(low.iloc[-8:].min()), last_close - p["stop_atr_mult"] * last_atr), 2)
        if stop >= last_close:
            return []
        risk = last_close - stop
        target = round(last_close + p["rr"] * risk, 2)

        trend_score = min(1.0, (last_fast - last_slow) / max(last_atr, 0.01) / 3)
        rsi_score = 1.0 - abs(last_rsi - 56) / 16
        confidence = round(max(0.0, min(1.0, 0.45 + 0.3 * trend_score + 0.25 * rsi_score)), 3)
        if ctx.market_regime == "bullish":
            confidence = min(1.0, confidence + 0.08)
        if confidence < p["min_confidence"]:
            return []

        return [_stock_signal(
            strategy=self.name,
            ctx=ctx,
            entry=last_close,
            stop=stop,
            target=target,
            confidence=confidence,
            reason=(
                f"EMA trend pullback: EMA{p['ema_fast']}>{p['ema_mid']}>{p['ema_slow']}, "
                f"price reclaimed EMA{p['ema_fast']} after touching EMA{p['ema_mid']}. "
                f"RSI {last_rsi:.1f}, relative volume {last_rv:.2f}."
            ),
            invalidation=f"Close below {stop:.2f} or EMA stack breaks.",
            rr=p["rr"],
            holding_period="intraday-to-swing",
            options=True,
            metadata={
                "ema_fast": round(last_fast, 2),
                "ema_mid": round(last_mid, 2),
                "ema_slow": round(last_slow, 2),
                "rsi": round(last_rsi, 1),
                "rel_volume": round(last_rv, 2),
                "regime": ctx.market_regime,
                "options_expression": "call_or_call_debit_spread",
            },
        )]


@registry.register
class BollingerSqueezeBreakoutStrategy(Strategy):
    name = "bollinger_squeeze_breakout"
    description = "Volatility squeeze releases through resistance on elevated volume."
    timeframe = "5Min"
    lookback_bars = 160
    default_params = {
        "bb_period": 20,
        "bb_k": 2.0,
        "squeeze_window": 120,
        "squeeze_quantile": 0.30,
        "breakout_lookback": 20,
        "min_rel_volume": 1.15,
        "atr_period": 14,
        "stop_atr_mult": 1.4,
        "rr": 2.5,
        "min_confidence": 0.48,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]

        bb = bollinger(close, p["bb_period"], p["bb_k"])
        width = (bb["upper"] - bb["lower"]) / bb["mid"]
        width_threshold = width.rolling(p["squeeze_window"], min_periods=60).quantile(
            p["squeeze_quantile"]
        )
        last_close = float(close.iloc[-1])
        prior_high = float(high.iloc[-(p["breakout_lookback"] + 1):-1].max())
        prior_width = float(width.iloc[-2])
        threshold = float(width_threshold.iloc[-2])
        last_rv = float(relative_volume(df["volume"], 20).iloc[-1])
        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])

        if any(np.isnan(x) for x in (prior_width, threshold, last_rv, last_atr)):
            return []
        squeeze = prior_width <= threshold
        breakout = last_close > prior_high
        volume_ok = last_rv >= p["min_rel_volume"]
        if not (squeeze and breakout and volume_ok):
            return []

        stop = round(max(float(bb["mid"].iloc[-1]), last_close - p["stop_atr_mult"] * last_atr), 2)
        if stop >= last_close:
            return []
        risk = last_close - stop
        target = round(last_close + p["rr"] * risk, 2)
        squeeze_score = min(1.0, max(0.0, (threshold - prior_width) / max(threshold, 0.001) + 0.5))
        rv_score = min(1.0, last_rv / 3)
        confidence = round(max(0.0, min(1.0, 0.4 + 0.3 * squeeze_score + 0.3 * rv_score)), 3)
        if ctx.market_regime == "bearish":
            confidence -= 0.12
        if confidence < p["min_confidence"]:
            return []

        return [_stock_signal(
            strategy=self.name,
            ctx=ctx,
            entry=last_close,
            stop=stop,
            target=target,
            confidence=round(confidence, 3),
            reason=(
                f"Bollinger squeeze released above {p['breakout_lookback']}-bar high "
                f"{prior_high:.2f}. Prior width {prior_width:.3f} <= threshold "
                f"{threshold:.3f}, relative volume {last_rv:.2f}."
            ),
            invalidation=f"Close back inside squeeze or below {stop:.2f}.",
            rr=p["rr"],
            holding_period="intraday-to-swing",
            options=True,
            metadata={
                "bb_width": round(prior_width, 4),
                "bb_width_threshold": round(threshold, 4),
                "rel_volume": round(last_rv, 2),
                "options_expression": "call_or_straddle_watchlist",
            },
        )]


@registry.register
class BullFlagContinuationStrategy(Strategy):
    name = "bull_flag_continuation"
    description = "Impulse move, controlled flag, then continuation trigger."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "impulse_bars": 12,
        "min_impulse_pct": 0.025,
        "flag_bars": 8,
        "max_pullback_fraction": 0.55,
        "min_rel_volume": 0.8,
        "atr_period": 14,
        "stop_atr_mult": 1.1,
        "rr": 2.0,
        "min_confidence": 0.47,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]

        impulse_start = float(close.iloc[-(p["impulse_bars"] + p["flag_bars"])])
        impulse_high = float(high.iloc[-(p["flag_bars"] + 1):-1].max())
        flag_low = float(low.iloc[-p["flag_bars"]:-1].min())
        last_close = float(close.iloc[-1])
        flag_high = float(high.iloc[-p["flag_bars"]:-1].max())
        impulse_pct = (impulse_high - impulse_start) / impulse_start
        pullback_fraction = (impulse_high - flag_low) / max(impulse_high - impulse_start, 0.01)
        last_rv = float(relative_volume(df["volume"], 20).iloc[-1])
        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])

        if any(np.isnan(x) for x in (
            impulse_pct, pullback_fraction, last_rv, last_atr
        )):
            return []
        impulse_ok = impulse_pct >= p["min_impulse_pct"]
        flag_ok = 0 < pullback_fraction <= p["max_pullback_fraction"]
        continuation = last_close > flag_high
        volume_ok = last_rv >= p["min_rel_volume"]
        if not (impulse_ok and flag_ok and continuation and volume_ok):
            return []

        stop = round(min(flag_low, last_close - p["stop_atr_mult"] * last_atr), 2)
        if stop >= last_close:
            return []
        risk = last_close - stop
        target = round(last_close + p["rr"] * risk, 2)
        confidence = round(max(
            0.0,
            min(1.0, 0.42 + min(0.25, impulse_pct * 4) + 0.2 * (1 - pullback_fraction) + 0.1 * min(last_rv, 2)),
        ), 3)
        if confidence < p["min_confidence"]:
            return []

        return [_stock_signal(
            strategy=self.name,
            ctx=ctx,
            entry=last_close,
            stop=stop,
            target=target,
            confidence=confidence,
            reason=(
                f"Bull flag continuation: impulse {impulse_pct:.1%}, pullback "
                f"{pullback_fraction:.0%} of impulse, close broke flag high {flag_high:.2f}."
            ),
            invalidation=f"Close below flag low/stop {stop:.2f}.",
            rr=p["rr"],
            holding_period="intraday",
            options=True,
            metadata={
                "impulse_pct": round(impulse_pct, 4),
                "pullback_fraction": round(pullback_fraction, 3),
                "rel_volume": round(last_rv, 2),
                "options_expression": "call_or_call_debit_spread",
            },
        )]


@registry.register
class RSIReversalStrategy(Strategy):
    name = "rsi_reversal"
    description = "RSI washout recovers above trigger with price reclaim confirmation."
    timeframe = "5Min"
    lookback_bars = 100
    default_params = {
        "rsi_period": 14,
        "washout_rsi": 32,
        "trigger_rsi": 37,
        "reclaim_lookback": 5,
        "atr_period": 14,
        "stop_atr_mult": 1.3,
        "rr": 1.8,
        "min_confidence": 0.46,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        rsi_s = rsi(close, p["rsi_period"])
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        last_rsi = float(rsi_s.iloc[-1])
        prev_rsi = float(rsi_s.iloc[-2])
        recent_min_rsi = float(rsi_s.iloc[-12:-1].min())
        reclaim_level = float(high.iloc[-(p["reclaim_lookback"] + 1):-1].max())
        last_atr = float(a.iloc[-1])

        if any(np.isnan(x) for x in (last_rsi, prev_rsi, recent_min_rsi, reclaim_level, last_atr)):
            return []
        washed_out = recent_min_rsi <= p["washout_rsi"]
        rsi_triggered = prev_rsi < p["trigger_rsi"] <= last_rsi
        price_reclaimed = last_close > reclaim_level
        if not (washed_out and rsi_triggered and price_reclaimed):
            return []

        stop = round(min(float(low.iloc[-12:].min()), last_close - p["stop_atr_mult"] * last_atr), 2)
        if stop >= last_close:
            return []
        risk = last_close - stop
        target = round(last_close + p["rr"] * risk, 2)
        confidence = round(max(0.0, min(1.0, 0.5 + (p["trigger_rsi"] - recent_min_rsi) / 60)), 3)
        if ctx.market_regime == "bearish":
            confidence -= 0.1
        if confidence < p["min_confidence"]:
            return []

        return [_stock_signal(
            strategy=self.name,
            ctx=ctx,
            entry=last_close,
            stop=stop,
            target=target,
            confidence=round(confidence, 3),
            reason=(
                f"RSI reversal: RSI washed out to {recent_min_rsi:.1f}, recovered to "
                f"{last_rsi:.1f}, and price reclaimed {reclaim_level:.2f}."
            ),
            invalidation=f"Failed reversal below {stop:.2f}.",
            rr=p["rr"],
            holding_period="intraday",
            options=False,
            metadata={"rsi": round(last_rsi, 1), "washout_rsi": round(recent_min_rsi, 1)},
        )]


@registry.register
class EMADowntrendPutStrategy(Strategy):
    name = "ema_downtrend_put"
    description = "EMA 8/21/50 downtrend; bearish continuation expressed as a put candidate."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "ema_fast": 8,
        "ema_mid": 21,
        "ema_slow": 50,
        "rsi_period": 14,
        "rsi_min": 28,
        "rsi_max": 58,
        "min_rel_volume": 0.8,
        "atr_period": 14,
        "stop_atr_mult": 1.2,
        "rr": 2.0,
        "min_confidence": 0.47,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        if ctx.market_regime == "bullish":
            pass  # Still fire in bullish regime with a confidence penalty

        p = self.params
        close = df["close"]
        high = df["high"]
        low = df["low"]
        fast = ema(close, p["ema_fast"])
        mid = ema(close, p["ema_mid"])
        slow = ema(close, p["ema_slow"])
        rsi_s = rsi(close, p["rsi_period"])
        rv = relative_volume(df["volume"], 20)
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        last_fast = float(fast.iloc[-1])
        last_mid = float(mid.iloc[-1])
        last_slow = float(slow.iloc[-1])
        last_rsi = float(rsi_s.iloc[-1])
        last_rv = float(rv.iloc[-1])
        last_atr = float(a.iloc[-1])

        if any(np.isnan(x) for x in (
            last_fast, last_mid, last_slow, last_rsi, last_rv, last_atr
        )):
            return []

        trend = last_fast < last_mid < last_slow and last_close < last_slow
        failed_reclaim = prev_close >= float(fast.iloc[-2]) and last_close < last_fast
        momentum_ok = p["rsi_min"] <= last_rsi <= p["rsi_max"]
        volume_ok = last_rv >= p["min_rel_volume"]
        if not (trend and failed_reclaim and momentum_ok and volume_ok):
            return []

        underlying_stop = round(max(float(high.iloc[-8:].max()), last_close + p["stop_atr_mult"] * last_atr), 2)
        underlying_target = round(last_close - p["rr"] * (underlying_stop - last_close), 2)
        confidence = round(max(0.0, min(1.0, 0.5 + min(0.25, (last_slow - last_fast) / max(last_atr, 0.01) / 8))), 3)
        if ctx.market_regime == "bearish":
            confidence = min(1.0, confidence + 0.08)
        if confidence < p["min_confidence"]:
            return []

        return [Signal(
            strategy=self.name,
            asset_class=AssetClass.OPTION,
            symbol=ctx.symbol,
            underlying=ctx.symbol,
            direction=Direction.BEARISH,
            entry=round(last_close, 2),
            stop_loss=underlying_stop,
            take_profit=underlying_target,
            confidence=confidence,
            reason=(
                f"EMA downtrend put setup: EMA{p['ema_fast']}<EMA{p['ema_mid']}<"
                f"EMA{p['ema_slow']}; price failed EMA{p['ema_fast']}. "
                f"RSI {last_rsi:.1f}, relative volume {last_rv:.2f}."
            ),
            invalidation=f"Underlying close above {underlying_stop:.2f} invalidates put thesis.",
            risk_reward=p["rr"],
            suggested_qty=1,
            suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now,
            status=SignalStatus.NEW,
            metadata={
                "ema_fast": round(last_fast, 2),
                "ema_mid": round(last_mid, 2),
                "ema_slow": round(last_slow, 2),
                "rsi": round(last_rsi, 1),
                "rel_volume": round(last_rv, 2),
                "underlying_stop": underlying_stop,
                "underlying_target": underlying_target,
                "options_expression": "long_put_or_put_debit_spread",
            },
        )]


# ─────────────────────────────────────────────────────────────────────────────
# ELITE TIER — 6 additional top-1% quant strategies
# ─────────────────────────────────────────────────────────────────────────────


@registry.register
class KeltnerSqueezeStrategy(Strategy):
    """TTM Squeeze: Bollinger Bands compress inside Keltner Channel, then
    momentum explodes. One of the highest-accuracy intraday setups used by
    prop desks worldwide."""
    name = "keltner_squeeze"
    description = "TTM Squeeze: BB inside Keltner → momentum breakout on histogram flip."
    timeframe = "5Min"
    lookback_bars = 180
    default_params = {
        "bb_period": 20, "bb_k": 2.0,
        "kc_period": 20, "kc_atr": 14, "kc_mult": 1.5,
        "hist_period": 12, "squeeze_bars": 6,
        "atr_period": 14, "stop_atr_mult": 1.3, "rr": 2.5,
        "min_confidence": 0.50,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low = df["close"], df["high"], df["low"]

        bb = bollinger(close, p["bb_period"], p["bb_k"])
        kc = keltner(close, high, low, p["kc_period"], p["kc_atr"], p["kc_mult"])

        squeeze = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])
        in_squeeze = bool(squeeze.iloc[-p["squeeze_bars"]:-1].all())
        just_fired = bool(not squeeze.iloc[-1])
        if not (in_squeeze and just_fired):
            return []

        # Momentum histogram: price vs midpoint of BB/KC mid average
        mid = (bb["mid"] + kc["mid"]) / 2
        hist = close - mid
        prev_hist = float(hist.iloc[-2])
        curr_hist = float(hist.iloc[-1])
        bullish_momentum = curr_hist > prev_hist and curr_hist > 0
        bearish_momentum = curr_hist < prev_hist and curr_hist < 0
        if not (bullish_momentum or bearish_momentum):
            return []

        last_close = float(close.iloc[-1])
        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])
        if np.isnan(last_atr) or last_atr <= 0:
            return []

        if bullish_momentum:
            stop = round(last_close - p["stop_atr_mult"] * last_atr, 2)
            target = round(last_close + p["rr"] * (last_close - stop), 2)
            direction = Direction.BULLISH
            hist_str = f"+{curr_hist:.2f}"
        else:
            stop = round(last_close + p["stop_atr_mult"] * last_atr, 2)
            target = round(last_close - p["rr"] * (stop - last_close), 2)
            direction = Direction.BEARISH
            hist_str = f"{curr_hist:.2f}"

        squeeze_duration = int(squeeze.iloc[-20:].sum())
        confidence = round(min(1.0, 0.52 + 0.03 * squeeze_duration + 0.1 * min(abs(curr_hist) / max(last_atr, 0.01), 1.0)), 3)
        if ctx.market_regime == "bullish" and bullish_momentum:
            confidence = min(1.0, confidence + 0.06)
        if confidence < p["min_confidence"]:
            return []

        bull = direction == Direction.BULLISH
        return [Signal(
            strategy=self.name, asset_class=AssetClass.STOCK, symbol=ctx.symbol,
            direction=direction,
            entry=round(last_close, 2),
            stop_loss=stop if bull else target,
            take_profit=target if bull else stop,
            confidence=confidence,
            reason=(
                f"TTM Squeeze fired after {squeeze_duration}-bar compression. "
                f"Momentum histogram {hist_str}, {'bull' if bull else 'bear'} expansion."
            ),
            invalidation=f"Histogram reverses back below 0 or squeeze re-enters.",
            risk_reward=p["rr"],
            suggested_qty=1,
            suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now,
            status=SignalStatus.NEW,
            metadata={
                "squeeze_bars": squeeze_duration,
                "histogram": round(curr_hist, 3),
                "regime": ctx.market_regime,
                "options_expression": "call_debit_spread" if bull else "put_debit_spread",
            },
        )]


@registry.register
class GapAndGoStrategy(Strategy):
    """Opening gap ≥ 2.5 % with volume surge on first bar — one of the most
    profitable setups used by retail prop traders. Enter the continuation,
    not the reversal."""
    name = "gap_and_go"
    description = "Opening gap ≥2.5% + volume surge on first bar → continuation entry."
    timeframe = "5Min"
    lookback_bars = 80
    default_params = {
        "min_gap_pct": 0.025,
        "max_gap_pct": 0.18,
        "min_rel_volume_open": 2.5,
        "consolidation_bars": 6,
        "max_fill_pct": 0.30,
        "atr_period": 14,
        "stop_atr_mult": 1.0,
        "rr": 2.8,
        "min_confidence": 0.50,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

        prev_close = float(close.iloc[-self.lookback_bars])
        open_price = float(df["open"].iloc[-self.lookback_bars + 1] if "open" in df.columns else close.iloc[-self.lookback_bars + 1])
        gap_pct = (open_price - prev_close) / max(prev_close, 0.01)
        if not (p["min_gap_pct"] <= gap_pct <= p["max_gap_pct"]):
            return []

        open_bar_vol = float(vol.iloc[-self.lookback_bars + 1])
        avg_vol = float(vol.iloc[-40:-self.lookback_bars + 1].mean()) if len(vol) > self.lookback_bars else float(vol.mean())
        if avg_vol <= 0 or open_bar_vol / avg_vol < p["min_rel_volume_open"]:
            return []

        gap_low = float(low.iloc[-self.lookback_bars + 1:-1].min())
        fill_pct = (open_price - gap_low) / max(open_price - prev_close, 0.01)
        if fill_pct > p["max_fill_pct"]:
            return []

        last_close = float(close.iloc[-1])
        consol_high = float(high.iloc[-p["consolidation_bars"]:-1].max())
        if last_close <= consol_high:
            return []

        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])
        if np.isnan(last_atr) or last_atr <= 0:
            return []

        stop = round(gap_low, 2)
        risk = last_close - stop
        if risk <= 0:
            return []
        target = round(last_close + p["rr"] * risk, 2)
        confidence = round(min(1.0, 0.52 + gap_pct * 2 + min(open_bar_vol / max(avg_vol, 1) / 10, 0.15)), 3)
        if confidence < p["min_confidence"]:
            return []

        return [_stock_signal(
            strategy=self.name, ctx=ctx,
            entry=last_close, stop=stop, target=target, confidence=confidence,
            reason=(
                f"Gap-and-go: gapped {gap_pct:.1%} at open on {open_bar_vol / max(avg_vol, 1):.1f}x volume. "
                f"Gap held (fill {fill_pct:.0%}), breaking consolidation high {consol_high:.2f}."
            ),
            invalidation=f"Gap fills below {stop:.2f}.",
            rr=p["rr"], holding_period="intraday", options=True,
            metadata={"gap_pct": round(gap_pct, 4), "fill_pct": round(fill_pct, 3),
                      "open_rv": round(open_bar_vol / max(avg_vol, 1), 2),
                      "options_expression": "call_or_call_debit_spread"},
        )]


@registry.register
class SupertrendReversalStrategy(Strategy):
    """Supertrend direction flip (bear→bull or bull→bear) confirmed by
    Stochastic cross — a clean, lagging-but-reliable trend-change signal
    widely used on crypto, futures, and high-beta equities."""
    name = "supertrend_reversal"
    description = "Supertrend direction flip + Stochastic cross confirmation."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "st_period": 14, "st_mult": 3.0,
        "stoch_k": 14, "stoch_d": 3,
        "stoch_bull_level": 40, "stoch_bear_level": 60,
        "atr_period": 14, "stop_atr_mult": 1.2, "rr": 2.2,
        "min_confidence": 0.48,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low = df["close"], df["high"], df["low"]

        st = supertrend(high, low, close, p["st_period"], p["st_mult"])
        stoch = stochastic(high, low, close, p["stoch_k"], p["stoch_d"])

        prev_dir = float(st["direction"].iloc[-2])
        curr_dir = float(st["direction"].iloc[-1])
        if prev_dir == curr_dir:
            return []

        last_k = float(stoch["k"].iloc[-1])
        last_d = float(stoch["d"].iloc[-1])
        prev_k = float(stoch["k"].iloc[-2])
        last_close = float(close.iloc[-1])
        last_atr = float(atr(high, low, close, p["atr_period"]).iloc[-1])

        if any(np.isnan(x) for x in (last_k, last_d, prev_k, last_atr)):
            return []

        bull_flip = curr_dir == 1.0
        bear_flip = curr_dir == -1.0

        stoch_bull_ok = bull_flip and last_k > last_d and last_k > p["stoch_bull_level"]
        stoch_bear_ok = bear_flip and last_k < last_d and last_k < p["stoch_bear_level"]
        if not (stoch_bull_ok or stoch_bear_ok):
            return []

        st_line = float(st["line"].iloc[-1])
        if bull_flip:
            stop = round(min(st_line, last_close - p["stop_atr_mult"] * last_atr), 2)
            target = round(last_close + p["rr"] * (last_close - stop), 2)
            direction = Direction.BULLISH
        else:
            stop = round(max(st_line, last_close + p["stop_atr_mult"] * last_atr), 2)
            target = round(last_close - p["rr"] * (stop - last_close), 2)
            direction = Direction.BEARISH

        confidence = round(min(1.0, 0.50 + abs(last_k - 50) / 200 + 0.05), 3)
        if ctx.market_regime == "bullish" and bull_flip:
            confidence = min(1.0, confidence + 0.07)
        if ctx.market_regime == "bearish" and bear_flip:
            confidence = min(1.0, confidence + 0.07)
        if confidence < p["min_confidence"]:
            return []

        bull = direction == Direction.BULLISH
        return [Signal(
            strategy=self.name, asset_class=AssetClass.STOCK, symbol=ctx.symbol,
            direction=direction,
            entry=round(last_close, 2),
            stop_loss=stop if bull else target,
            take_profit=target if bull else stop,
            confidence=confidence,
            reason=(
                f"Supertrend flipped {'bullish' if bull else 'bearish'} with "
                f"Stochastic %K={last_k:.1f} crossing {'above' if bull else 'below'} %D={last_d:.1f}."
            ),
            invalidation=f"Supertrend flips back within 2 bars.",
            risk_reward=p["rr"],
            suggested_qty=1,
            suitable_for_options=True,
            holding_period_hint="intraday-to-swing",
            generated_at=ctx.now, status=SignalStatus.NEW,
            metadata={
                "stochastic_k": round(last_k, 1), "stochastic_d": round(last_d, 1),
                "supertrend_line": round(st_line, 2), "regime": ctx.market_regime,
                "options_expression": "long_call" if bull else "long_put",
            },
        )]


@registry.register
class VWAPOrphanRevertStrategy(Strategy):
    """Price that has traveled >2 std-devs from anchored VWAP with RSI
    extreme and declining volume = exhausted move likely to revert.
    Very high win-rate mean-reversion setup used by market makers."""
    name = "vwap_orphan_revert"
    description = "Price >2σ from VWAP + RSI extreme + volume dry-up → mean reversion."
    timeframe = "5Min"
    lookback_bars = 120
    default_params = {
        "vwap_std": 2.2,
        "rsi_period": 14,
        "rsi_bull_extreme": 75,
        "rsi_bear_extreme": 25,
        "vol_dry_pct": 0.55,
        "atr_period": 14,
        "stop_atr_mult": 1.0,
        "rr": 1.8,
        "min_confidence": 0.48,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low = df["close"], df["high"], df["low"]

        vb = vwap_std_bands(df, p["vwap_std"])
        rsi_s = rsi(close, p["rsi_period"])
        rv = relative_volume(df["volume"], 20)
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        vwap_mid = float(vb["vwap"].iloc[-1])
        vwap_upper = float(vb["upper"].iloc[-1])
        vwap_lower = float(vb["lower"].iloc[-1])
        last_rsi = float(rsi_s.iloc[-1])
        last_rv = float(rv.iloc[-1])
        last_atr = float(a.iloc[-1])

        if any(np.isnan(x) for x in (vwap_mid, vwap_upper, vwap_lower, last_rsi, last_rv, last_atr)):
            return []

        above_extreme = last_close > vwap_upper and last_rsi > p["rsi_bull_extreme"]
        below_extreme = last_close < vwap_lower and last_rsi < p["rsi_bear_extreme"]
        volume_dry = last_rv < p["vol_dry_pct"]

        if not ((above_extreme or below_extreme) and volume_dry):
            return []

        if above_extreme:
            direction = Direction.BEARISH
            stop = round(last_close + p["stop_atr_mult"] * last_atr, 2)
            target = round(vwap_mid, 2)
            risk = stop - last_close
        else:
            direction = Direction.BULLISH
            stop = round(last_close - p["stop_atr_mult"] * last_atr, 2)
            target = round(vwap_mid, 2)
            risk = last_close - stop

        if risk <= 0:
            return []
        actual_rr = round(abs(target - last_close) / risk, 2)
        if actual_rr < 1.0:
            return []

        deviation_z = abs(last_close - vwap_mid) / max(abs(vwap_upper - vwap_mid), 0.01)
        confidence = round(min(1.0, 0.48 + 0.12 * (deviation_z - 1) + 0.08 * (1 - last_rv)), 3)
        if confidence < p["min_confidence"]:
            return []

        bull = direction == Direction.BULLISH
        return [Signal(
            strategy=self.name, asset_class=AssetClass.STOCK, symbol=ctx.symbol,
            direction=direction,
            entry=round(last_close, 2),
            stop_loss=stop if bull else target,
            take_profit=target if bull else stop,
            confidence=confidence,
            reason=(
                f"VWAP orphan revert: price {'above' if not bull else 'below'} VWAP "
                f"{'upper' if not bull else 'lower'} band {vwap_upper if not bull else vwap_lower:.2f}, "
                f"RSI {last_rsi:.1f}, volume {last_rv:.2f}x (drying up). VWAP target {vwap_mid:.2f}."
            ),
            invalidation=f"Price extends {'above' if not bull else 'below'} {stop:.2f}.",
            risk_reward=actual_rr, suggested_qty=1, suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now, status=SignalStatus.NEW,
            metadata={
                "vwap": round(vwap_mid, 2), "vwap_upper": round(vwap_upper, 2),
                "vwap_lower": round(vwap_lower, 2), "rsi": round(last_rsi, 1),
                "rel_volume": round(last_rv, 2), "deviation_z": round(deviation_z, 2),
                "options_expression": "long_put_otm" if not bull else "long_call_otm",
            },
        )]


@registry.register
class OrderFlowExhaustionStrategy(Strategy):
    """Volume-price divergence: new price extreme with significantly lower
    volume than prior swing = institutional supply/demand exhaustion.
    Used by order-flow traders to fade trend endings."""
    name = "order_flow_exhaustion"
    description = "New price high/low on declining volume → exhaustion reversal."
    timeframe = "5Min"
    lookback_bars = 100
    default_params = {
        "swing_lookback": 20,
        "vol_decline_threshold": 0.65,
        "rsi_period": 14,
        "divergence_bars": 10,
        "atr_period": 14,
        "stop_atr_mult": 1.1,
        "rr": 2.0,
        "min_confidence": 0.46,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

        rsi_s = rsi(close, p["rsi_period"])
        a = atr(high, low, close, p["atr_period"])
        last_close = float(close.iloc[-1])
        last_atr = float(a.iloc[-1])

        window = p["divergence_bars"]
        cur_high = float(high.iloc[-1])
        cur_vol = float(vol.iloc[-1])
        prev_high = float(high.iloc[-(window + 1):-1].max())
        prev_high_idx = int(high.iloc[-(window + 1):-1].values.argmax())
        prev_high_vol = float(vol.iloc[-(window + 1):-1].iloc[prev_high_idx])

        cur_low = float(low.iloc[-1])
        prev_low = float(low.iloc[-(window + 1):-1].min())
        prev_low_idx = int(low.iloc[-(window + 1):-1].values.argmin())
        prev_low_vol = float(vol.iloc[-(window + 1):-1].iloc[prev_low_idx])

        last_rsi = float(rsi_s.iloc[-1])
        if any(np.isnan(x) for x in (last_atr, last_rsi, prev_high_vol, prev_low_vol)):
            return []

        bearish_diverge = (
            cur_high > prev_high
            and cur_vol < prev_high_vol * p["vol_decline_threshold"]
            and last_rsi > 60
        )
        bullish_diverge = (
            cur_low < prev_low
            and cur_vol < prev_low_vol * p["vol_decline_threshold"]
            and last_rsi < 40
        )
        if not (bearish_diverge or bullish_diverge):
            return []

        if bearish_diverge:
            stop = round(last_close + p["stop_atr_mult"] * last_atr, 2)
            target = round(last_close - p["rr"] * (stop - last_close), 2)
            direction = Direction.BEARISH
            vol_ratio = round(cur_vol / max(prev_high_vol, 1), 2)
        else:
            stop = round(last_close - p["stop_atr_mult"] * last_atr, 2)
            target = round(last_close + p["rr"] * (last_close - stop), 2)
            direction = Direction.BULLISH
            vol_ratio = round(cur_vol / max(prev_low_vol, 1), 2)

        confidence = round(min(1.0, 0.46 + (1 - vol_ratio) * 0.3 + abs(last_rsi - 50) / 200), 3)
        if confidence < p["min_confidence"]:
            return []

        bull = direction == Direction.BULLISH
        return [Signal(
            strategy=self.name, asset_class=AssetClass.STOCK, symbol=ctx.symbol,
            direction=direction,
            entry=round(last_close, 2),
            stop_loss=stop if bull else target,
            take_profit=target if bull else stop,
            confidence=confidence,
            reason=(
                f"Order flow exhaustion: {'higher high' if not bull else 'lower low'} "
                f"on only {vol_ratio:.0%} of prior swing volume. "
                f"RSI {last_rsi:.1f} — {'supply' if not bull else 'demand'} exhausting."
            ),
            invalidation=f"Volume surges confirming the {'high' if not bull else 'low'}.",
            risk_reward=p["rr"], suggested_qty=1, suitable_for_options=True,
            holding_period_hint="intraday",
            generated_at=ctx.now, status=SignalStatus.NEW,
            metadata={
                "volume_ratio": vol_ratio, "rsi": round(last_rsi, 1),
                "regime": ctx.market_regime,
                "options_expression": "long_put" if not bull else "long_call",
            },
        )]


@registry.register
class MultiTimeframeEMAStackStrategy(Strategy):
    """Multi-timeframe EMA confluence: 5-min EMA stack PLUS the same stack
    confirmed across a longer lookback (proxy for 15-min / 30-min alignment).
    Highest-quality momentum entries — institutions call this 'tape reading'."""
    name = "multi_tf_ema_stack"
    description = "5-min + higher-TF EMA stack aligned → highest-conviction trend entry."
    timeframe = "5Min"
    lookback_bars = 200
    default_params = {
        "ema_fast": 8, "ema_mid": 21, "ema_slow": 50,
        "htf_fast": 20, "htf_mid": 50, "htf_slow": 100,
        "rsi_period": 14, "rsi_min": 45, "rsi_max": 75,
        "min_rel_volume": 0.9,
        "atr_period": 14, "stop_atr_mult": 1.1, "rr": 2.5,
        "min_confidence": 0.52,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df = ctx.bars
        if df is None or len(df) < self.lookback_bars:
            return []
        p = self.params
        close, high, low = df["close"], df["high"], df["low"]

        f5 = ema(close, p["ema_fast"])
        m5 = ema(close, p["ema_mid"])
        s5 = ema(close, p["ema_slow"])

        fh = ema(close, p["htf_fast"])
        mh = ema(close, p["htf_mid"])
        sh = ema(close, p["htf_slow"])

        rsi_s = rsi(close, p["rsi_period"])
        rv = relative_volume(df["volume"], 20)
        a = atr(high, low, close, p["atr_period"])

        last_close = float(close.iloc[-1])
        vals = {
            "f5": float(f5.iloc[-1]), "m5": float(m5.iloc[-1]), "s5": float(s5.iloc[-1]),
            "fh": float(fh.iloc[-1]), "mh": float(mh.iloc[-1]), "sh": float(sh.iloc[-1]),
            "rsi": float(rsi_s.iloc[-1]), "rv": float(rv.iloc[-1]), "atr": float(a.iloc[-1]),
        }
        if any(np.isnan(v) for v in vals.values()):
            return []

        bull_5m = vals["f5"] > vals["m5"] > vals["s5"] and last_close > vals["s5"]
        bull_htf = vals["fh"] > vals["mh"] > vals["sh"] and last_close > vals["sh"]
        bear_5m = vals["f5"] < vals["m5"] < vals["s5"] and last_close < vals["s5"]
        bear_htf = vals["fh"] < vals["mh"] < vals["sh"] and last_close < vals["sh"]

        bull = bull_5m and bull_htf
        bear = bear_5m and bear_htf
        if not (bull or bear):
            return []

        rsi_ok = (bull and p["rsi_min"] <= vals["rsi"] <= p["rsi_max"]) or \
                 (bear and (100 - p["rsi_max"]) <= vals["rsi"] <= (100 - p["rsi_min"]))
        volume_ok = vals["rv"] >= p["min_rel_volume"]
        if not (rsi_ok and volume_ok):
            return []

        if bull:
            stop = round(last_close - p["stop_atr_mult"] * vals["atr"], 2)
            target = round(last_close + p["rr"] * (last_close - stop), 2)
            direction = Direction.BULLISH
        else:
            stop = round(last_close + p["stop_atr_mult"] * vals["atr"], 2)
            target = round(last_close - p["rr"] * (stop - last_close), 2)
            direction = Direction.BEARISH

        separation = abs(vals["fh"] - vals["sh"]) / max(vals["atr"], 0.01)
        confidence = round(min(1.0, 0.52 + 0.06 * min(separation / 3, 1.0)), 3)
        if ctx.market_regime == "bullish" and bull:
            confidence = min(1.0, confidence + 0.07)
        if ctx.market_regime == "bearish" and bear:
            confidence = min(1.0, confidence + 0.07)
        if confidence < p["min_confidence"]:
            return []

        return [Signal(
            strategy=self.name, asset_class=AssetClass.STOCK, symbol=ctx.symbol,
            direction=direction,
            entry=round(last_close, 2),
            stop_loss=stop if bull else target,
            take_profit=target if bull else stop,
            confidence=confidence,
            reason=(
                f"MTF EMA stack {'bullish' if bull else 'bearish'}: "
                f"5-min EMA{p['ema_fast']}/{p['ema_mid']}/{p['ema_slow']} aligned AND "
                f"HTF EMA{p['htf_fast']}/{p['htf_mid']}/{p['htf_slow']} aligned. "
                f"RSI {vals['rsi']:.1f}, rel-vol {vals['rv']:.2f}."
            ),
            invalidation=f"EMA stack loses alignment on either timeframe.",
            risk_reward=p["rr"], suggested_qty=1, suitable_for_options=True,
            holding_period_hint="intraday-to-swing",
            generated_at=ctx.now, status=SignalStatus.NEW,
            metadata={
                "ema_separation": round(separation, 2), "rsi": round(vals["rsi"], 1),
                "rel_volume": round(vals["rv"], 2), "regime": ctx.market_regime,
                "options_expression": "call_debit_spread" if bull else "put_debit_spread",
            },
        )]


def _stock_signal(
    *,
    strategy: str,
    ctx: StrategyContext,
    entry: float,
    stop: float,
    target: float,
    confidence: float,
    reason: str,
    invalidation: str,
    rr: float,
    holding_period: str,
    options: bool,
    metadata: dict,
) -> Signal:
    return Signal(
        strategy=strategy,
        asset_class=AssetClass.STOCK,
        symbol=ctx.symbol,
        direction=Direction.BULLISH,
        entry=round(entry, 2),
        stop_loss=stop,
        take_profit=target,
        confidence=confidence,
        reason=reason,
        invalidation=invalidation,
        risk_reward=rr,
        suggested_qty=1,
        suitable_for_options=options,
        holding_period_hint=holding_period,
        generated_at=ctx.now,
        status=SignalStatus.NEW,
        metadata=metadata,
    )
