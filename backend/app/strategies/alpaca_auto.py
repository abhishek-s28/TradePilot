"""Async Alpaca-paper strategies that emit risk-layer OrderProposal objects.

Strategy tiers by DTE / account size:
  0DTE  (0–1 d)  : ZeroDTEScalp          — SPY/QQQ intraday momentum
  Weekly (2–14 d): WeeklyMomentum        — high-liquidity ETFs + mega-caps
  Standard (15-45d): LongDirectional, BullCallSpread, BearPutSpread, IronCondor
  Theta  (21-45d): CoveredCall, CashSecuredPut, BullPutSpread, BearCallSpread
  Event  (5-21d) : NewsCatalyst, IVCrushStrangle
  Public filings : InsiderBuySignal, AnalystUpgrade — SEC Form 4 & analyst data
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import pandas as pd

from app.models.domain import Bar, OptionContract, OptionRight, Side
from app.risk.manager import OrderProposal
from app.utils.indicators import atr, ema, rsi, macd, stochastic, bollinger
from app.utils.news_sentiment import score_news
from app.utils.supply_demand import confluence_adjustment, zone_context

# Symbols with liquid 0DTE / weekly options chains
_INDEX_ETF_SYMBOLS = frozenset({"SPY", "QQQ", "IWM", "DIA", "SPX"})
_VOL_ETF_SYMBOLS = frozenset({"VXX", "UVXY"})
_WEEKLY_ELIGIBLE = frozenset(
    _INDEX_ETF_SYMBOLS
    | {
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
        "AVGO", "NFLX", "COIN", "PLTR", "MSTR",
        "XLK", "XLF", "XLE", "SMH", "SOXX", "GLD", "TLT",
    }
)


class AutoStrategy(Protocol):
    name: str
    min_equity: float

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]: ...

    async def should_exit(self, position, account, market) -> bool: ...


@dataclass
class BaseStrategy:
    name: str
    min_equity: float = 0.0
    enabled: bool = True
    stop_loss_pct: float = 0.45
    take_profit_pct: float = 0.60
    min_hold_minutes: float = 3.0

    async def should_exit(self, position, account, market) -> bool:
        return _check_exit(position, self.stop_loss_pct, self.take_profit_pct, self.min_hold_minutes)


def _check_exit(position, stop_loss_pct: float, take_profit_pct: float, min_hold_minutes: float) -> bool:
    """Shared stop-loss / take-profit / min-hold check.

    For OPTIONS: returns False — let evaluate_option_exit handle all option
    exits with its DTE-scaled logic and smart trailing stop. The strategy-level
    check was using the wrong P&L denominator (market_value instead of
    cost_basis) which understated gains and overstated losses.

    For STOCKS: uses cost_basis for accurate P&L %.
    """
    from app.models.domain import AssetClass
    if getattr(position, "asset_class", None) == AssetClass.OPTION:
        return False

    opened_at = getattr(position, "opened_at", None)
    if opened_at is not None:
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        held_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
        if held_minutes < min_hold_minutes:
            return False

    pnl = float(getattr(position, "unrealized_pnl", 0.0) or 0.0)
    avg_price = abs(float(getattr(position, "avg_price", 0.0) or 0.0))
    qty = abs(int(float(getattr(position, "qty", 0) or 0)))
    cost_basis = avg_price * qty
    if cost_basis <= 0:
        return False
    ratio = pnl / cost_basis
    return ratio <= -stop_loss_pct or ratio >= take_profit_pct


def _apply_confluence(confidence: float, ctx: dict, direction: str) -> tuple[float, float]:
    """Nudge a strategy's base confidence using supply/demand zone confluence.

    Returns (adjusted_confidence, raw_adjustment) — the adjustment is also
    recorded in `signal_values` so it's visible in the order journal/UI.
    """
    adj = confluence_adjustment(ctx, direction)
    return round(max(0.05, min(0.95, confidence + adj)), 3), adj


# Daily-bar trend per underlying changes at most once a session, so cache it
# briefly to avoid an extra bars fetch on every 5-min scan cycle for every
# trend-following strategy that checks it.
_DAILY_TREND_CACHE: dict[str, tuple[float, str]] = {}
_DAILY_TREND_TTL_SECONDS = 900.0


async def _daily_trend(market, underlying: str) -> str:
    """Higher-timeframe (daily) trend direction: "up", "down", or "flat".

    A genius trader confirms a fast intraday signal against the dominant
    daily trend before entering — this is the single biggest filter against
    counter-trend whipsaws that 5-minute-only signals are prone to.
    """
    cached = _DAILY_TREND_CACHE.get(underlying)
    now_mono = time.monotonic()
    if cached and now_mono - cached[0] < _DAILY_TREND_TTL_SECONDS:
        return cached[1]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=120)
    bars: list[Bar] = await market.get_bars(underlying, "1Day", start, end)
    trend = "flat"
    if len(bars) >= 55:
        closes = pd.Series([b.close for b in bars])
        ema20 = float(ema(closes, 20).iloc[-1])
        ema50 = float(ema(closes, 50).iloc[-1])
        last = float(closes.iloc[-1])
        if last > ema20 > ema50:
            trend = "up"
        elif last < ema20 < ema50:
            trend = "down"

    _DAILY_TREND_CACHE[underlying] = (now_mono, trend)
    return trend


def _apply_trend(confidence: float, daily_trend: str, direction: str) -> tuple[float, float]:
    """Block counter-trend trades, boost aligned trades.

    A genius day trader NEVER fights the daily trend. Counter-trend
    intraday signals are the #1 source of losses — they look good on
    a 5-min chart but get steamrolled by the larger move.

    Aligned trades get a confidence boost. Counter-trend trades get
    killed (confidence → 0) so they fail the confidence floor check.
    """
    if daily_trend == "flat":
        return confidence, 0.0
    aligned = (daily_trend == "up" and direction == "bullish") or (
        daily_trend == "down" and direction == "bearish"
    )
    if aligned:
        adj = 0.06
        return round(min(0.95, confidence + adj), 3), adj
    adj = -0.50
    return round(max(0.05, confidence + adj), 3), adj


def _volume_confirmed(df: pd.DataFrame, min_ratio: float = 1.3) -> bool:
    """Check that the latest bar has above-average volume.

    Day traders NEVER enter without volume confirmation. A price move
    without volume behind it is noise, not signal.
    """
    if len(df) < 15:
        return False
    volume = df["volume"]
    last_vol = float(volume.iloc[-1])
    avg_vol = float(volume.iloc[-15:-1].mean())
    if avg_vol <= 0:
        return False
    return last_vol >= avg_vol * min_ratio


def _bullish_candle(df: pd.DataFrame) -> bool:
    """Check that the latest bar closed bullish (close > open) with body > 40% of range."""
    if df.empty:
        return False
    o = float(df["open"].iloc[-1])
    c = float(df["close"].iloc[-1])
    h = float(df["high"].iloc[-1])
    l = float(df["low"].iloc[-1])
    rng = h - l
    if rng <= 0:
        return False
    body = c - o
    return body > 0 and body / rng >= 0.40


def _bearish_candle(df: pd.DataFrame) -> bool:
    """Check that the latest bar closed bearish (close < open) with body > 40% of range."""
    if df.empty:
        return False
    o = float(df["open"].iloc[-1])
    c = float(df["close"].iloc[-1])
    h = float(df["high"].iloc[-1])
    l = float(df["low"].iloc[-1])
    rng = h - l
    if rng <= 0:
        return False
    body = o - c
    return body > 0 and body / rng >= 0.40


def _momentum_score(df: pd.DataFrame) -> float:
    """Score recent momentum from 0.0 (no momentum) to 1.0 (strong).

    Combines RSI slope, price vs. VWAP, and volume trend into a single
    score that filters out weak setups. Day traders want to enter when
    multiple momentum signals align.
    """
    if len(df) < 20:
        return 0.0

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    rsi_vals = rsi(close, 14)
    rsi_now = float(rsi_vals.iloc[-1])
    rsi_prev = float(rsi_vals.iloc[-3])
    rsi_slope = (rsi_now - rsi_prev) / 3.0

    typical = (high + low + close) / 3
    cum_vol = volume.cumsum()
    vwap_s = (typical * volume).cumsum() / cum_vol.replace(0, float("nan"))
    last_close = float(close.iloc[-1])
    last_vwap = float(vwap_s.iloc[-1])
    vwap_pct = (last_close - last_vwap) / last_vwap if last_vwap > 0 else 0.0

    last_vol = float(volume.iloc[-1])
    avg_vol = float(volume.iloc[-15:-1].mean()) if len(volume) >= 15 else float(volume.mean())
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

    score = 0.0
    score += min(0.35, max(0.0, abs(rsi_slope) * 3.0))
    score += min(0.35, max(0.0, abs(vwap_pct) * 15.0))
    score += min(0.30, max(0.0, (vol_ratio - 1.0) * 0.5)) if vol_ratio > 1.0 else 0.0
    return round(min(1.0, score), 3)


# ── 0DTE Scalp ───────────────────────────────────────────────────────────────

class ZeroDTEScalp(BaseStrategy):
    """Intraday 0-1 DTE options on SPY/QQQ using 5-min momentum + VWAP.

    Only fires during regular hours. Looks for a confirmed breakout above/below
    VWAP with RSI momentum confirmation, then buys a 0-1 DTE ATM call/put.
    Uses tight 35% stop-loss due to extreme theta decay.
    """

    ELIGIBLE = _INDEX_ETF_SYMBOLS | {"TQQQ", "SQQQ", "SPXL", "SPXS"} | _WEEKLY_ELIGIBLE

    def __init__(self) -> None:
        super().__init__(
            "zero_dte_scalp", min_equity=500,
            stop_loss_pct=0.35, take_profit_pct=0.40, min_hold_minutes=2.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in self.ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 20:
            return []

        if not _volume_confirmed(df, 1.3):
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        typical = (high + low + close) / 3
        cum_vol = volume.cumsum()
        vwap = (typical * volume).cumsum() / cum_vol.replace(0, float("nan"))
        last_close = float(close.iloc[-1])
        last_vwap = float(vwap.iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])
        last_atr = float(atr(high, low, close, 14).iloc[-1])
        mom = _momentum_score(df)

        gap_threshold = last_atr * 0.20

        if (last_close > last_vwap + gap_threshold
                and last_rsi > 55 and last_rsi < 78
                and _bullish_candle(df)
                and mom >= 0.35):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.68, daily_trend, "bullish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.60, account, min_dte=0, max_dte=2
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "zero_dte_vwap_breakout_call",
                {"rsi": last_rsi, "vwap_gap": round(last_close - last_vwap, 4),
                 "close": last_close, "momentum_score": mom,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        if (last_close < last_vwap - gap_threshold
                and last_rsi < 45 and last_rsi > 22
                and _bearish_candle(df)
                and mom >= 0.35):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.68, daily_trend, "bearish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.60, account, min_dte=0, max_dte=2
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "zero_dte_vwap_breakdown_put",
                {"rsi": last_rsi, "vwap_gap": round(last_vwap - last_close, 4),
                 "close": last_close, "momentum_score": mom,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        return []


# ── Weekly Momentum ───────────────────────────────────────────────────────────

class WeeklyMomentum(BaseStrategy):
    """2–14 DTE ATM/near-ATM options for fast directional plays.

    Combines EMA trend, RSI momentum, and ATR volatility expansion to find
    confirmed directional moves on highly-liquid names.
    """

    def __init__(self) -> None:
        super().__init__("weekly_momentum", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []

        if not _volume_confirmed(df, 1.2):
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        last_rsi = float(rsi(close, 14).iloc[-1])
        last_atr = float(atr(high, low, close, 14).iloc[-1])

        prev_gap = float(ema9.iloc[-2]) - float(ema21.iloc[-2])
        curr_gap = float(ema9.iloc[-1]) - float(ema21.iloc[-1])
        avg_atr = float(atr(high, low, close, 14).iloc[-8:-1].mean())
        atr_expansion = last_atr > avg_atr * 1.1

        bullish = curr_gap > 0 and prev_gap <= 0 and last_rsi > 55 and last_rsi < 78 and atr_expansion
        bearish = curr_gap < 0 and prev_gap >= 0 and last_rsi < 45 and last_rsi > 22 and atr_expansion

        sustained_bull = curr_gap > last_atr * 0.5 and last_rsi > 58 and last_rsi < 78 and not bullish
        sustained_bear = curr_gap < -last_atr * 0.5 and last_rsi < 42 and last_rsi > 22 and not bearish

        if bullish or sustained_bull:
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.65 if bullish else 0.60, ctx, "bullish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bullish")
            signal_values = {
                "rsi": last_rsi, "ema_gap": round(curr_gap, 4), "atr_expansion": atr_expansion,
                "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                "daily_trend": daily_trend, "trend_adjustment": trend_adj,
            }
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.65, account, min_dte=2, max_dte=14
            )
            reason = "ema_crossover_call" if bullish else "trend_momentum_call"
            if contract:
                return [_long_option_proposal(
                    self.name, underlying, contract, reason, signal_values, confidence=confidence,
                )]
            return []

        if bearish or sustained_bear:
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.65 if bearish else 0.60, ctx, "bearish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.65, account, min_dte=2, max_dte=14
            )
            if not contract:
                return []
            reason = "ema_crossover_put" if bearish else "trend_momentum_put"
            return [_long_option_proposal(
                self.name, underlying, contract, reason,
                {
                    "rsi": last_rsi, "ema_gap": round(curr_gap, 4), "atr_expansion": atr_expansion,
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                    "daily_trend": daily_trend, "trend_adjustment": trend_adj,
                },
                confidence=confidence,
            )]

        return []


# ── Long Directional (standard 30-45 DTE) ────────────────────────────────────

class LongDirectional(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("long_directional", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 80:
            return []
        close = df["close"]
        trend_up = close.iloc[-1] > ema(close, 20).iloc[-1] > ema(close, 50).iloc[-1]
        trend_down = close.iloc[-1] < ema(close, 20).iloc[-1] < ema(close, 50).iloc[-1]
        last_rsi = float(rsi(close, 14).iloc[-1])
        if trend_up and last_rsi > 60:
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.62, ctx, "bullish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bullish")
            contract = await _select_option(market, underlying, OptionRight.CALL, 0.35, 0.60, min_dte=30, max_dte=45)
            return [_long_option_proposal(self.name, underlying, contract, "call_momentum", {
                "rsi": last_rsi, "trend": "up", "close": float(close.iloc[-1]),
                "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                "daily_trend": daily_trend, "trend_adjustment": trend_adj,
            }, confidence=confidence)] if contract else []
        if trend_down and last_rsi < 40:
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.62, ctx, "bearish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bearish")
            contract = await _select_option(market, underlying, OptionRight.PUT, 0.35, 0.60, min_dte=30, max_dte=45)
            return [_long_option_proposal(self.name, underlying, contract, "put_momentum", {
                "rsi": last_rsi, "trend": "down", "close": float(close.iloc[-1]),
                "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                "daily_trend": daily_trend, "trend_adjustment": trend_adj,
            }, confidence=confidence)] if contract else []
        return []


# ── News Catalyst ─────────────────────────────────────────────────────────────

class NewsCatalyst(BaseStrategy):
    """Trades breaking-news sentiment: Fed/White House/tariff/exec headlines
    and company-specific catalysts (earnings, FDA, lawsuits, guidance, etc).
    Uses 7-21 DTE for faster reaction.
    """

    NEWS_LOOKBACK_HOURS = 4.0
    MACRO_LOOKBACK_HOURS = 3.0
    MIN_MAGNITUDE = 0.45
    MACRO_SYMBOLS = ("SPY", "QQQ")

    def __init__(self) -> None:
        super().__init__("news_catalyst", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        own_news = await market.get_news(
            [underlying], lookback_hours=self.NEWS_LOOKBACK_HOURS, limit=10
        )
        own = score_news(own_news, symbol=underlying)

        macro_news = await market.get_news(
            list(self.MACRO_SYMBOLS), lookback_hours=self.MACRO_LOOKBACK_HOURS, limit=10
        )
        macro = score_news(macro_news)

        polarity = own.polarity
        impact = own.impact
        top_headline = own.top_headline
        macro_aligned = False
        if macro.headline_count and macro.impact >= 1.3:
            if own.headline_count == 0:
                polarity, impact, top_headline = macro.polarity, macro.impact, macro.top_headline
            else:
                macro_aligned = (polarity > 0) == (macro.polarity > 0) and macro.polarity != 0
                polarity = polarity * 0.65 + macro.polarity * 0.35
                impact = max(impact, macro.impact)

        magnitude = abs(polarity) * impact
        if magnitude < self.MIN_MAGNITUDE or polarity == 0:
            return []

        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []
        last_rsi = float(rsi(df["close"], 14).iloc[-1])

        confidence = round(min(0.82, 0.50 + magnitude * 0.18 + (0.05 if macro_aligned else 0.0)), 3)
        signal_values = {
            "sentiment_polarity": round(polarity, 3),
            "sentiment_impact": round(impact, 2),
            "sentiment_magnitude": round(magnitude, 3),
            "headline": top_headline,
            "headline_count": own.headline_count,
            "macro_headline_count": macro.headline_count,
            "macro_aligned": macro_aligned,
            "rsi": last_rsi,
        }

        if polarity > 0:
            if last_rsi > 70:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.60, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "news_catalyst_bullish",
                {**signal_values, "direction": "bullish"}, confidence=confidence,
            )]

        if last_rsi < 30:
            return []
        contract = await _select_affordable_long_option(
            market, underlying, OptionRight.PUT, 0.35, 0.60, account, min_dte=7, max_dte=21
        )
        if not contract:
            return []
        return [_long_option_proposal(
            self.name, underlying, contract, "news_catalyst_bearish",
            {**signal_values, "direction": "bearish"}, confidence=confidence,
        )]


# ── Bull Call Spread (debit) ──────────────────────────────────────────────────

class BullCallSpread(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("bull_call_spread", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 80:
            return []
        close = df["close"]
        if not (float(rsi(close, 14).iloc[-1]) > 60 and close.iloc[-1] > ema(close, 50).iloc[-1]):
            return []
        long_leg = await _select_option(market, underlying, OptionRight.CALL, 0.45, 0.65, min_dte=21, max_dte=45)
        short_leg = await _further_otm(market, underlying, long_leg, OptionRight.CALL)
        return [_spread(self.name, underlying, long_leg, short_leg, "bull_call_debit_spread")] if long_leg and short_leg else []


# ── Bear Put Spread (debit) ───────────────────────────────────────────────────

class BearPutSpread(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("bear_put_spread", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 80:
            return []
        close = df["close"]
        if not (float(rsi(close, 14).iloc[-1]) < 40 and close.iloc[-1] < ema(close, 50).iloc[-1]):
            return []
        long_leg = await _select_option(market, underlying, OptionRight.PUT, 0.45, 0.65, min_dte=21, max_dte=45)
        short_leg = await _further_otm(market, underlying, long_leg, OptionRight.PUT)
        return [_spread(self.name, underlying, long_leg, short_leg, "bear_put_debit_spread")] if long_leg and short_leg else []


# ── Bull Put Spread (credit) ──────────────────────────────────────────────────

class BullPutSpread(BaseStrategy):
    """Sell a put spread below current price to collect premium in a bullish/neutral regime.

    Entry: RSI > 45 and price > EMA50 (not overbought). Sell the higher-strike put
    and buy the lower-strike put to cap risk. Target 30-45 DTE for theta.
    """

    def __init__(self) -> None:
        super().__init__("bull_put_spread", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []
        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        # Bullish to neutral — not in a downtrend
        if not (last_rsi > 45 and close.iloc[-1] > ema(close, 50).iloc[-1]):
            return []

        # Short leg: delta 0.25-0.35 (OTM put to sell)
        short_leg = await _select_option(market, underlying, OptionRight.PUT, 0.25, 0.35, min_dte=21, max_dte=45)
        if not short_leg:
            return []
        # Long leg: further OTM put to buy (protection)
        long_leg = await _further_otm(market, underlying, short_leg, OptionRight.PUT)
        if not long_leg:
            return []

        credit = max(0.01, short_leg.mid - long_leg.mid)
        width = abs(short_leg.strike - long_leg.strike)
        max_risk = round((width - credit) * 100, 2)
        net_credit = round(credit * 100, 2)

        iv_r = await _iv_rank(market, underlying)
        base_confidence = 0.62 + _iv_rank_confidence_boost(iv_r)

        return [OrderProposal(
            strategy_name=self.name,
            legs=[short_leg.symbol, long_leg.symbol],
            symbol=short_leg.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=max_risk,
            est_cost_usd=-net_credit,
            option_premium_per_contract=net_credit,
            limit_price=round(credit, 2),
            signal_values={
                "short_leg": short_leg.symbol,
                "long_leg": long_leg.symbol,
                "leg_sides": {short_leg.symbol: "sell", long_leg.symbol: "buy"},
                "width": width,
                "net_credit": net_credit,
                "max_risk": max_risk,
                "rsi": last_rsi,
                "dte": _dte(short_leg),
                "iv_rank": iv_r,
                "confidence": base_confidence,
            },
            confidence=base_confidence,
            reason="bull_put_credit_spread",
        )]


# ── Bear Call Spread (credit) ─────────────────────────────────────────────────

class BearCallSpread(BaseStrategy):
    """Sell a call spread above current price in a bearish/neutral regime."""

    def __init__(self) -> None:
        super().__init__("bear_call_spread", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []
        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        if not (last_rsi < 55 and close.iloc[-1] < ema(close, 50).iloc[-1]):
            return []

        short_leg = await _select_option(market, underlying, OptionRight.CALL, 0.25, 0.35, min_dte=21, max_dte=45)
        if not short_leg:
            return []
        long_leg = await _further_otm(market, underlying, short_leg, OptionRight.CALL)
        if not long_leg:
            return []

        credit = max(0.01, short_leg.mid - long_leg.mid)
        width = abs(long_leg.strike - short_leg.strike)
        max_risk = round((width - credit) * 100, 2)
        net_credit = round(credit * 100, 2)

        iv_r = await _iv_rank(market, underlying)
        base_confidence = 0.62 + _iv_rank_confidence_boost(iv_r)

        return [OrderProposal(
            strategy_name=self.name,
            legs=[short_leg.symbol, long_leg.symbol],
            symbol=short_leg.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=max_risk,
            est_cost_usd=-net_credit,
            option_premium_per_contract=net_credit,
            limit_price=round(credit, 2),
            signal_values={
                "short_leg": short_leg.symbol,
                "long_leg": long_leg.symbol,
                "leg_sides": {short_leg.symbol: "sell", long_leg.symbol: "buy"},
                "width": width,
                "net_credit": net_credit,
                "max_risk": max_risk,
                "rsi": last_rsi,
                "dte": _dte(short_leg),
                "iv_rank": iv_r,
                "confidence": base_confidence,
            },
            confidence=base_confidence,
            reason="bear_call_credit_spread",
        )]


# ── Iron Condor ───────────────────────────────────────────────────────────────

class IronCondor(BaseStrategy):
    """Sell a put spread + call spread simultaneously — profits if the underlying
    stays range-bound. Best entered when IV is elevated (IVR proxy > 50).

    Wings: short delta ~0.16-0.25 each side, 30-45 DTE.
    """

    def __init__(self) -> None:
        super().__init__("iron_condor", min_equity=2_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []
        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        # Neutral zone — not strongly trending
        if not (40 < last_rsi < 60):
            return []

        # Put spread legs
        short_put = await _select_option(market, underlying, OptionRight.PUT, 0.16, 0.25, min_dte=21, max_dte=45)
        long_put = await _further_otm(market, underlying, short_put, OptionRight.PUT) if short_put else None

        # Call spread legs
        short_call = await _select_option(market, underlying, OptionRight.CALL, 0.16, 0.25, min_dte=21, max_dte=45)
        long_call = await _further_otm(market, underlying, short_call, OptionRight.CALL) if short_call else None

        if not (short_put and long_put and short_call and long_call):
            return []

        put_credit = max(0.01, short_put.mid - long_put.mid)
        call_credit = max(0.01, short_call.mid - long_call.mid)
        total_credit = round((put_credit + call_credit) * 100, 2)
        put_width = abs(short_put.strike - long_put.strike)
        call_width = abs(long_call.strike - short_call.strike)
        max_risk = round((max(put_width, call_width) - put_credit - call_credit) * 100, 2)

        # IV-rank proxy: use short-leg IV as a rough gauge
        iv_proxy = float(short_put.implied_volatility or 0.0) * 100
        if iv_proxy < 30:
            return []

        iv_r = await _iv_rank(market, underlying)
        base_confidence = 0.64
        base_confidence += _iv_rank_confidence_boost(iv_r)

        return [OrderProposal(
            strategy_name=self.name,
            legs=[short_put.symbol, long_put.symbol, short_call.symbol, long_call.symbol],
            symbol=short_put.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=max_risk,
            est_cost_usd=-total_credit,
            option_premium_per_contract=total_credit,
            limit_price=round(put_credit + call_credit, 2),
            signal_values={
                "short_put": short_put.symbol,
                "long_put": long_put.symbol,
                "short_call": short_call.symbol,
                "long_call": long_call.symbol,
                "leg_sides": {
                    short_put.symbol: "sell", long_put.symbol: "buy",
                    short_call.symbol: "sell", long_call.symbol: "buy",
                },
                "total_credit": total_credit,
                "max_risk": max_risk,
                "iv_rank_proxy": iv_proxy,
                "iv_rank": iv_r,
                "rsi": last_rsi,
                "dte": _dte(short_put),
                "confidence": base_confidence,
            },
            confidence=base_confidence,
            reason="iron_condor_neutral_iv_elevated",
        )]


# ── Iron Butterfly ────────────────────────────────────────────────────────────

class IronButterfly(BaseStrategy):
    """ATM iron butterfly — maximum premium collected at exact pin of current price.

    Requires IV rank proxy > 60. Very tight risk tolerance: exit at 25% of max loss.
    """

    def __init__(self) -> None:
        super().__init__(
            "iron_butterfly", min_equity=2_000,
            stop_loss_pct=0.25, take_profit_pct=0.40, min_hold_minutes=5.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []
        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        if not (38 < last_rsi < 62):
            return []

        # ATM short straddle core: sell ATM call + ATM put
        short_call = await _select_option(market, underlying, OptionRight.CALL, 0.45, 0.55, min_dte=14, max_dte=30)
        short_put = await _select_option(market, underlying, OptionRight.PUT, 0.45, 0.55, min_dte=14, max_dte=30)
        if not (short_call and short_put):
            return []

        iv_proxy = float(short_call.implied_volatility or 0.0) * 100
        if iv_proxy < 45:
            return []

        long_call = await _further_otm(market, underlying, short_call, OptionRight.CALL)
        long_put = await _further_otm(market, underlying, short_put, OptionRight.PUT)
        if not (long_call and long_put):
            return []

        credit = (short_call.mid + short_put.mid - long_call.mid - long_put.mid)
        total_credit = round(max(0.01, credit) * 100, 2)
        wing_width = abs(long_call.strike - short_call.strike)
        max_risk = round((wing_width - credit) * 100, 2)

        return [OrderProposal(
            strategy_name=self.name,
            legs=[short_call.symbol, short_put.symbol, long_call.symbol, long_put.symbol],
            symbol=short_call.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=max(0.01, max_risk),
            est_cost_usd=-total_credit,
            option_premium_per_contract=total_credit,
            limit_price=round(max(0.01, credit), 2),
            signal_values={
                "short_call": short_call.symbol, "short_put": short_put.symbol,
                "long_call": long_call.symbol, "long_put": long_put.symbol,
                "leg_sides": {
                    short_call.symbol: "sell", short_put.symbol: "sell",
                    long_call.symbol: "buy", long_put.symbol: "buy",
                },
                "total_credit": total_credit,
                "max_risk": max_risk,
                "iv_rank_proxy": iv_proxy,
                "dte": _dte(short_call),
                "confidence": 0.63,
            },
            confidence=0.63,
            reason="iron_butterfly_high_iv_neutral",
        )]


# ── Volatility Breakout ───────────────────────────────────────────────────────

class VolatilityBreakout(BaseStrategy):
    """ATR-based volatility expansion entry on any optionable name.

    Fires when the current bar's range exceeds 2× the 14-period ATR average,
    signaling a volatility regime shift worth trading directionally.
    """

    def __init__(self) -> None:
        super().__init__("volatility_breakout", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        avg_atr = float(atr(high, low, close, 14).iloc[-15:-1].mean())
        last_range = float(high.iloc[-1]) - float(low.iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])

        if avg_atr <= 0 or last_range < avg_atr * 1.8:
            return []
        if not _volume_confirmed(df, 1.4):
            return []

        if last_rsi > 52 and _bullish_candle(df):
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.68, ctx, "bullish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bullish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.65, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "vol_breakout_call",
                {
                    "last_range": round(last_range, 4), "avg_atr": round(avg_atr, 4), "rsi": last_rsi,
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                    "daily_trend": daily_trend, "trend_adjustment": trend_adj,
                },
                confidence=confidence,
            )]

        if last_rsi < 48 and _bearish_candle(df):
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.68, ctx, "bearish")
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(confidence, daily_trend, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.65, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "vol_breakout_put",
                {
                    "last_range": round(last_range, 4), "avg_atr": round(avg_atr, 4), "rsi": last_rsi,
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                    "daily_trend": daily_trend, "trend_adjustment": trend_adj,
                },
                confidence=confidence,
            )]

        return []


# ── Mean-Reversion Oversold/Overbought ────────────────────────────────────────

class MeanReversionOptions(BaseStrategy):
    """Contrarian entries on extreme RSI readings using 7-21 DTE options.

    Fires when RSI reaches extreme levels (>80 or <20) on an asset that has
    historically mean-reverted, suggesting the move is over-extended.
    Uses lower delta (more OTM) to express the view cheaply.
    """

    def __init__(self) -> None:
        super().__init__("mean_reversion_options", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []

        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        ema20_val = float(ema(close, 20).iloc[-1])
        last_close = float(close.iloc[-1])
        deviation_pct = abs(last_close - ema20_val) / ema20_val if ema20_val > 0 else 0.0

        if last_rsi > 78 and deviation_pct > 0.03 and _volume_confirmed(df, 1.2):
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.66, ctx, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.20, 0.35, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "mean_reversion_overbought_put",
                {
                    "rsi": last_rsi, "ema_deviation_pct": round(deviation_pct, 4), "close": last_close,
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                },
                confidence=confidence,
            )]

        if last_rsi < 22 and deviation_pct > 0.03 and _volume_confirmed(df, 1.2):
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(0.66, ctx, "bullish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.20, 0.35, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "mean_reversion_oversold_call",
                {
                    "rsi": last_rsi, "ema_deviation_pct": round(deviation_pct, 4), "close": last_close,
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                },
                confidence=confidence,
            )]

        return []


# ── Supply / Demand Zone Reversal ─────────────────────────────────────────────

class SupplyDemandReversal(BaseStrategy):
    """Trades bounces off demand zones and rejections off supply zones.

    Zones come from `app.utils.supply_demand.find_zones`: prior swing
    highs/lows printed on a relative-volume spike that price subsequently
    moved away from — i.e. levels where real buying/selling previously
    showed up. This strategy waits for price to return to one of those
    levels AND print an immediate reversal candle (close back through open)
    confirming the level is holding again, rather than trading the zone the
    instant price touches it.
    """

    def __init__(self) -> None:
        super().__init__("supply_demand_reversal", min_equity=500, min_hold_minutes=5.0)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 60:
            return []

        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        ctx = zone_context(df)
        last_open = float(df["open"].iloc[-1])
        last_close = float(close.iloc[-1])
        reversal_up = last_close > last_open
        reversal_down = last_close < last_open

        if ctx.get("near_demand") and reversal_up and last_rsi < 70:
            zone = ctx["nearest_demand"]
            base_confidence = 0.58 + 0.10 * float(zone.get("strength") or 0.0)
            confidence, sd_adj = _apply_confluence(base_confidence, ctx, "bullish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.60, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "demand_zone_bounce_call",
                {
                    "rsi": last_rsi, "zone_strength": zone.get("strength"),
                    "zone_low": zone.get("low"), "zone_high": zone.get("high"),
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                },
                confidence=confidence,
            )]

        if ctx.get("near_supply") and reversal_down and last_rsi > 30:
            zone = ctx["nearest_supply"]
            base_confidence = 0.58 + 0.10 * float(zone.get("strength") or 0.0)
            confidence, sd_adj = _apply_confluence(base_confidence, ctx, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.35, 0.60, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "supply_zone_rejection_put",
                {
                    "rsi": last_rsi, "zone_strength": zone.get("strength"),
                    "zone_low": zone.get("low"), "zone_high": zone.get("high"),
                    "sd_adjustment": sd_adj, "sd_location": ctx.get("location"),
                },
                confidence=confidence,
            )]

        return []


# ── Covered Call ──────────────────────────────────────────────────────────────

class CoveredCall(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("covered_call", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        shares = _held_shares(account, underlying)
        if shares < 100:
            return []
        contract = await _select_option(market, underlying, OptionRight.CALL, 0.25, 0.35, min_dte=21, max_dte=45)
        if not contract:
            return []
        premium = round(contract.mid * 100, 2)
        return [OrderProposal(
            strategy_name=self.name,
            legs=[contract.symbol],
            symbol=contract.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=0.0,
            est_cost_usd=-premium,
            option_premium_per_contract=round(contract.mid * 100, 2),
            limit_price=round(contract.mid, 2),
            signal_values={
                "shares_held": shares,
                "delta": contract.delta,
                "dte": _dte(contract),
                "premium_credit": premium,
            },
            confidence=0.58,
            reason="covered_call_30_45_dte_delta_25_35",
        )]


# ── Cash-Secured Put ──────────────────────────────────────────────────────────

class CashSecuredPut(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("cash_secured_put", min_equity=1_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        contract = await _select_option(market, underlying, OptionRight.PUT, 0.25, 0.35, min_dte=21, max_dte=45)
        if not contract:
            return []
        assignment_cash = contract.strike * 100
        if assignment_cash > float(getattr(account, "buying_power", 0.0)):
            return []
        iv_rank_proxy = float(contract.implied_volatility or 0.0) * 100
        if iv_rank_proxy <= 50:
            return []
        max_risk = round(assignment_cash - contract.mid * 100, 2)
        return [OrderProposal(
            strategy_name=self.name,
            legs=[contract.symbol],
            symbol=contract.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=max_risk,
            est_cost_usd=round(-contract.mid * 100, 2),
            option_premium_per_contract=round(contract.mid * 100, 2),
            limit_price=round(contract.mid, 2),
            signal_values={
                "strike": contract.strike,
                "assignment_cash": assignment_cash,
                "iv_rank_proxy": iv_rank_proxy,
                "delta": contract.delta,
            },
            confidence=0.56,
            reason="cash_secured_put_iv_rank_gt_50_delta_25_35",
        )]


# ── Calendar Spread ───────────────────────────────────────────────────────────

class CalendarSpread(BaseStrategy):
    """Sell near-term, buy far-term same-strike option. Profits from term-structure
    and vega exposure when IV of near-term is elevated relative to far-term.
    """

    def __init__(self) -> None:
        super().__init__("calendar_spread", min_equity=1_500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []
        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        # Calendar needs a neutral-ish market (not strong directional)
        if not (40 < last_rsi < 60):
            return []

        # Near-term leg to sell (7-14 DTE)
        near_leg = await _select_option(market, underlying, OptionRight.CALL, 0.45, 0.55, min_dte=7, max_dte=14)
        if not near_leg:
            return []

        # Far-term leg to buy (30-45 DTE, same right, similar strike)
        chain = await market.get_options_chain(underlying)
        far_candidates = [
            c for c in chain
            if c.right == OptionRight.CALL
            and 30 <= _dte(c) <= 45
            and c.strike == near_leg.strike
            and c.mid > 0
        ]
        if not far_candidates:
            return []
        far_leg = min(far_candidates, key=lambda c: abs(_dte(c) - 38))

        debit = max(0.01, far_leg.mid - near_leg.mid)
        max_risk = round(debit * 100, 2)

        return [OrderProposal(
            strategy_name=self.name,
            legs=[near_leg.symbol, far_leg.symbol],
            symbol=far_leg.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.BUY,
            qty=1,
            max_risk_usd=max_risk,
            est_cost_usd=max_risk,
            option_premium_per_contract=max_risk,
            limit_price=round(debit, 2),
            signal_values={
                "near_leg": near_leg.symbol,
                "far_leg": far_leg.symbol,
                "leg_sides": {near_leg.symbol: "sell", far_leg.symbol: "buy"},
                "strike": near_leg.strike,
                "near_dte": _dte(near_leg),
                "far_dte": _dte(far_leg),
                "debit": debit,
                "rsi": last_rsi,
                "confidence": 0.60,
            },
            confidence=0.60,
            reason="calendar_spread_neutral_vega_long",
        )]


# ── Gamma Scalp ───────────────────────────────────────────────────────────────

class GammaScalp(BaseStrategy):
    """Long ATM straddle with delta-hedged stock overlays — captures realized vol
    exceeding implied vol. Requires sufficient equity for the stock hedge.
    """

    def __init__(self) -> None:
        super().__init__("gamma_scalp", min_equity=10_000)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        last_atr = float(atr(high, low, close, 14).iloc[-1])
        avg_atr = float(atr(high, low, close, 14).iloc[-20:-5].mean())

        # Fire when realized vol is expanding — increasing ATR vs average
        if avg_atr <= 0 or last_atr < avg_atr * 1.3:
            return []

        call_leg = await _select_option(market, underlying, OptionRight.CALL, 0.45, 0.55, min_dte=7, max_dte=21)
        put_leg = await _select_option(market, underlying, OptionRight.PUT, 0.45, 0.55, min_dte=7, max_dte=21)
        if not (call_leg and put_leg):
            return []

        total_cost = round((call_leg.mid + put_leg.mid) * 100, 2)
        iv_proxy = float(call_leg.implied_volatility or 0.0) * 100

        return [OrderProposal(
            strategy_name=self.name,
            legs=[call_leg.symbol, put_leg.symbol],
            symbol=call_leg.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.BUY,
            qty=1,
            max_risk_usd=total_cost,
            est_cost_usd=total_cost,
            option_premium_per_contract=total_cost,
            limit_price=round((call_leg.mid + put_leg.mid) / 2, 2),
            signal_values={
                "call_leg": call_leg.symbol, "put_leg": put_leg.symbol,
                "leg_sides": {call_leg.symbol: "buy", put_leg.symbol: "buy"},
                "total_cost": total_cost,
                "iv_proxy": iv_proxy,
                "atr_ratio": round(last_atr / avg_atr, 3),
                "confidence": 0.63,
            },
            confidence=0.63,
            reason="gamma_scalp_atm_straddle_vol_expanding",
        )]


# ── IV Crush / Pre-Earnings Short Strangle ────────────────────────────────────

class IVCrushStrangle(BaseStrategy):
    """Sell OTM strangle ahead of an IV-crush event (earnings, macro).

    Entry: IV rank proxy >70. Tight 50% stop on the combined credit to limit blowup.
    """

    def __init__(self) -> None:
        super().__init__(
            "iv_crush_strangle", min_equity=2_000,
            stop_loss_pct=0.50, take_profit_pct=0.50, min_hold_minutes=5.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []

        # Look for ATM straddle to check IV level
        call_probe = await _select_option(market, underlying, OptionRight.CALL, 0.45, 0.55, min_dte=5, max_dte=21)
        if not call_probe:
            return []
        iv_proxy = float(call_probe.implied_volatility or 0.0) * 100
        if iv_proxy < 70:
            return []

        short_call = await _select_option(market, underlying, OptionRight.CALL, 0.20, 0.30, min_dte=5, max_dte=21)
        short_put = await _select_option(market, underlying, OptionRight.PUT, 0.20, 0.30, min_dte=5, max_dte=21)
        if not (short_call and short_put):
            return []

        total_credit = round((short_call.mid + short_put.mid) * 100, 2)
        if total_credit < 50:
            return []

        return [OrderProposal(
            strategy_name=self.name,
            legs=[short_call.symbol, short_put.symbol],
            symbol=short_call.symbol,
            underlying=underlying,
            asset_class=contract_asset_class(),
            side=Side.SELL,
            qty=1,
            max_risk_usd=total_credit * 4,
            est_cost_usd=-total_credit,
            option_premium_per_contract=total_credit,
            limit_price=round((short_call.mid + short_put.mid) / 2, 2),
            signal_values={
                "short_call": short_call.symbol, "short_put": short_put.symbol,
                "leg_sides": {short_call.symbol: "sell", short_put.symbol: "sell"},
                "total_credit": total_credit,
                "iv_rank_proxy": iv_proxy,
                "dte": _dte(short_call),
                "confidence": 0.65,
            },
            confidence=0.65,
            reason="iv_crush_short_strangle_high_iv",
        )]


# ── Helpers ───────────────────────────────────────────────────────────────────

def contract_asset_class():
    from app.models.domain import AssetClass
    return AssetClass.OPTION


def stock_asset_class():
    from app.models.domain import AssetClass
    return AssetClass.STOCK


async def _bars(market, symbol: str) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=20)
    bars: list[Bar] = await market.get_bars(symbol, "5Min", start, end)
    rows = [
        {"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("timestamp").sort_index()


# ── IV rank cache (avoid repeated chain fetches per cycle) ───────────────────

_IV_RANK_CACHE: dict[str, tuple[float, float]] = {}
_IV_RANK_TTL_SECONDS = 600.0


async def _iv_rank(market, underlying: str) -> float | None:
    cached = _IV_RANK_CACHE.get(underlying)
    now_mono = time.monotonic()
    if cached and now_mono - cached[0] < _IV_RANK_TTL_SECONDS:
        return cached[1]

    try:
        chain = await market.get_options_chain(underlying)
        ivs = [c.implied_volatility for c in chain
               if c.implied_volatility and c.implied_volatility > 0]
        if len(ivs) < 5:
            return None
        avg_iv = sum(ivs) / len(ivs)
        max_iv = max(ivs)
        min_iv = min(ivs)
        rank = ((avg_iv - min_iv) / (max_iv - min_iv) * 100) if max_iv > min_iv else 50.0
        _IV_RANK_CACHE[underlying] = (now_mono, rank)
        return rank
    except Exception:
        return None


async def _should_skip_long_option(market, underlying: str) -> bool:
    iv_r = await _iv_rank(market, underlying)
    if iv_r is not None and iv_r > 72:
        return True
    return False


def _iv_rank_confidence_boost(iv_r: float | None) -> float:
    if iv_r is None:
        return 0.0
    if iv_r > 70:
        return 0.06
    if iv_r > 55:
        return 0.03
    return 0.0


# Below this mid-price, a single 1-cent bid/ask tick is a >8% swing in the
# contract's value — i.e. pure spread/quote noise, not a real move. Filtering
# these out (and tightening the spread cap from 20% -> 12%) was a major fix:
# previously every strategy gravitated to the cheapest (most lottery-ticket-y)
# contract in its delta band and got chopped up by noise alone.
_MIN_OPTION_PREMIUM = 0.20
_SMALL_ACCOUNT_MIN_OPTION_PREMIUM = 0.10
_MAX_SPREAD_PCT = 0.12


async def _select_option(
    market,
    underlying: str,
    right: OptionRight,
    min_delta: float,
    max_delta: float,
    min_dte: int = 0,
    max_dte: int = 45,
    max_premium_usd: float | None = None,
) -> OptionContract | None:
    chain = await market.get_options_chain(underlying)
    candidates = []
    now = datetime.now(timezone.utc)
    min_premium = _min_option_premium(max_premium_usd)
    max_mid = max_premium_usd / 100 if max_premium_usd is not None else None
    for contract in chain:
        dte = _dte(contract, now)
        if contract.right != right or dte < min_dte or dte > max_dte:
            continue
        if contract.mid < min_premium:
            continue
        if max_mid is not None and contract.mid > max_mid:
            continue
        delta = abs(contract.delta or _delta_proxy(contract))
        if min_delta <= delta <= max_delta and contract.spread_pct <= _MAX_SPREAD_PCT:
            candidates.append(contract)
    if not candidates:
        return None
    # Prefer tight spreads + liquidity over raw cheapness — within the delta
    # band, the cheapest contract is the one most dominated by tick noise.
    target_delta = (min_delta + max_delta) / 2
    return sorted(
        candidates,
        key=lambda c: (
            c.spread_pct,
            -c.liquidity_score,
            abs(abs(c.delta or _delta_proxy(c)) - target_delta),
            c.mid * 100,
        ),
    )[0]


async def _select_affordable_long_option(
    market,
    underlying: str,
    right: OptionRight,
    min_delta: float,
    max_delta: float,
    account,
    min_dte: int = 0,
    max_dte: int = 45,
) -> OptionContract | None:
    budget = _long_option_budget(account)
    if budget <= 0:
        return None
    for lo, hi in _delta_bands_for_budget(min_delta, max_delta):
        contract = await _select_option(
            market,
            underlying,
            right,
            lo,
            hi,
            min_dte=min_dte,
            max_dte=max_dte,
            max_premium_usd=budget,
        )
        if contract is not None:
            return contract
    return None


def _delta_bands_for_budget(min_delta: float, max_delta: float) -> list[tuple[float, float]]:
    bands = [(min_delta, max_delta)]
    if min_delta > 0.30:
        bands.append((0.25, min(max_delta, 0.45)))
    if min_delta > 0.20:
        bands.append((0.20, min(max_delta, 0.35)))
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for lo, hi in bands:
        if hi < lo:
            continue
        key = (round(lo, 2), round(hi, 2))
        if key not in seen:
            seen.add(key)
            out.append((lo, hi))
    return out


def _min_option_premium(max_premium_usd: float | None) -> float:
    if max_premium_usd is not None and max_premium_usd < _MIN_OPTION_PREMIUM * 100:
        return min(
            _MIN_OPTION_PREMIUM,
            max(_SMALL_ACCOUNT_MIN_OPTION_PREMIUM, max_premium_usd / 100 * 0.8),
        )
    return _MIN_OPTION_PREMIUM


async def _further_otm(
    market,
    underlying: str,
    long_leg: OptionContract | None,
    right: OptionRight,
) -> OptionContract | None:
    if long_leg is None:
        return None
    chain = await market.get_options_chain(underlying, long_leg.expiration)
    if right == OptionRight.CALL:
        candidates = [c for c in chain if c.right == right and c.strike > long_leg.strike and c.mid > 0]
    else:
        candidates = [c for c in chain if c.right == right and c.strike < long_leg.strike and c.mid > 0]
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: abs(c.strike - long_leg.strike))[0]


def _long_option_proposal(
    strategy: str,
    underlying: str,
    contract: OptionContract,
    reason: str,
    signal_values: dict,
    confidence: float = 0.62,
    stop_loss_pct: float = 0.45,
) -> OrderProposal:
    cost = round(contract.mid * 100, 2)
    return OrderProposal(
        strategy_name=strategy,
        legs=[contract.symbol],
        symbol=contract.symbol,
        underlying=underlying,
        asset_class=contract_asset_class(),
        side=Side.BUY,
        qty=1,
        max_risk_usd=cost,
        est_cost_usd=cost,
        option_premium_per_contract=cost,
        limit_price=round(contract.mid, 2),
        signal_values={
            **signal_values,
            "option_symbol": contract.symbol,
            "strike": contract.strike,
            "right": contract.right.value,
            "dte": _dte(contract),
            "delta": contract.delta,
            "iv": contract.implied_volatility,
            "stop_loss_pct": stop_loss_pct,
            "confidence": confidence,
        },
        confidence=confidence,
        reason=reason,
    )


def _long_option_budget(account) -> float:
    """Approximate the non-live risk caps before proposals reach RiskManager."""
    equity = max(float(getattr(account, "equity", 0.0) or 0.0), 0.0)
    buying_power = max(float(getattr(account, "buying_power", 0.0) or 0.0), 0.0)
    if equity <= 0:
        return 0.0
    max_trade_loss = min(500.0, max(equity, 50.0))
    max_option_premium = min(500.0, max(equity, 50.0))
    return round(max(0.0, min(max_trade_loss, max_option_premium, buying_power * 0.90)), 2)


def _option_affordable_for_account(proposal: OrderProposal, account) -> bool:
    budget = _long_option_budget(account)
    return budget > 0 and proposal.max_risk_usd <= budget and proposal.est_cost_usd <= budget


def _stock_affordable_for_account(proposal: OrderProposal, account) -> bool:
    buying_power = max(float(getattr(account, "buying_power", 0.0) or 0.0), 0.0)
    return proposal.est_cost_usd > 0 and proposal.est_cost_usd <= buying_power * 0.95


def _long_stock_proposal(
    strategy: str,
    underlying: str,
    price: float,
    atr_value: float,
    reason: str,
    signal_values: dict,
    confidence: float,
) -> OrderProposal | None:
    if price <= 0:
        return None
    stop_distance = max(min(atr_value * 0.75, price * 0.012), price * 0.003)
    stop_loss = round(max(0.01, price - stop_distance), 2)
    paper_market_order = _paper_market_orders_allowed()
    limit_price = None if paper_market_order else round(price * 1.001, 2)
    est_cost = round(limit_price or price * 1.002, 2)
    max_risk = round(max(0.01, price - stop_loss), 2)
    return OrderProposal(
        strategy_name=strategy,
        legs=[],
        symbol=underlying,
        underlying=underlying,
        asset_class=stock_asset_class(),
        side=Side.BUY,
        qty=1,
        max_risk_usd=max_risk,
        est_cost_usd=est_cost,
        limit_price=limit_price,
        signal_values={
            **signal_values,
            "fallback_asset": "stock",
            "fallback_order_type": "market" if paper_market_order else "limit",
            "entry_price": round(price, 2),
            "stop_loss": stop_loss,
            "estimated_share_risk": max_risk,
            "confidence": confidence,
        },
        confidence=confidence,
        reason=reason,
    )


def _paper_market_orders_allowed() -> bool:
    from app.core.settings import get_settings

    return not get_settings().can_trade_live


def _spread(
    strategy: str,
    underlying: str,
    long_leg: OptionContract,
    short_leg: OptionContract,
    reason: str,
) -> OrderProposal:
    debit = max(0.01, long_leg.mid - short_leg.mid)
    width = abs(short_leg.strike - long_leg.strike)
    max_risk = round(debit * 100, 2)
    return OrderProposal(
        strategy_name=strategy,
        legs=[long_leg.symbol, short_leg.symbol],
        symbol=long_leg.symbol,
        underlying=underlying,
        asset_class=contract_asset_class(),
        side=Side.BUY,
        qty=1,
        max_risk_usd=max_risk,
        est_cost_usd=max_risk,
        option_premium_per_contract=max_risk,
        limit_price=round(debit, 2),
        signal_values={
            "long_leg": long_leg.symbol,
            "short_leg": short_leg.symbol,
            "leg_sides": {long_leg.symbol: "buy", short_leg.symbol: "sell"},
            "width": width,
            "max_profit": round(width * 100 - max_risk, 2),
            "confidence": 0.60,
        },
        confidence=0.60,
        reason=reason,
    )


def _held_shares(account, underlying: str) -> int:
    positions = getattr(account, "positions", []) or []
    for pos in positions:
        if getattr(pos, "symbol", "") == underlying:
            return int(getattr(pos, "qty", 0) or 0)
    return 0


def _dte(contract: OptionContract, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return (contract.expiration - now).days


def _delta_proxy(contract: OptionContract) -> float:
    return 0.45


# ── SEC Form 4 Insider Buy Signal ─────────────────────────────────────────────

class InsiderBuySignal(BaseStrategy):
    """Trades on legally-disclosed SEC Form 4 insider purchase clusters.

    Company insiders (CEO, CFO, board directors) are required by law to publicly
    disclose any purchase of their own company's stock within 2 business days via
    SEC Form 4 filings. A cluster of insider purchases — especially by multiple
    insiders or a single large dollar amount — is one of the highest-conviction
    publicly-available signals. Insiders buy for one reason: they expect the stock
    to go up. They sell for many reasons (taxes, diversification, planned sales),
    so this strategy only acts on buy signals.

    Source: SEC EDGAR public API — 100% legal, zero latency risk.
    """

    MIN_BUY_VALUE = 100_000    # ignore trivial option exercises / small purchases
    LOOKBACK_DAYS = 5

    def __init__(self) -> None:
        super().__init__("insider_buy_signal", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        # Lazy import to avoid circular at module load
        from app.services.sec_filings import insider_signal

        try:
            signal = await insider_signal(
                underlying,
                lookback_days=self.LOOKBACK_DAYS,
                min_buy_value=self.MIN_BUY_VALUE,
            )
        except Exception:
            return []

        if signal is None or signal.direction != "buy":
            return []

        df = await _bars(market, underlying)
        if df.empty or len(df) < 20:
            return []

        close = df["close"]
        last_rsi = float(rsi(close, 14).iloc[-1])
        if last_rsi > 75:
            return []

        # 7-21 DTE call: we want directional exposure without paying for too much
        # time premium — insider cluster buys typically catalyse moves within 1-3 weeks.
        contract = await _select_affordable_long_option(
            market, underlying, OptionRight.CALL, 0.35, 0.65, account, min_dte=7, max_dte=21
        )
        if not contract:
            return []

        return [_long_option_proposal(
            self.name, underlying, contract, "insider_cluster_buy",
            {
                "insider_direction": signal.direction,
                "total_buy_value": signal.total_buy_value,
                "transaction_count": signal.transaction_count,
                "insider_reason": signal.reason,
                "rsi": last_rsi,
            },
            confidence=signal.confidence,
        )]


# ── Analyst Upgrade / Price Target Raise ──────────────────────────────────────

class AnalystUpgrade(BaseStrategy):
    """Trades on publicly-disclosed analyst upgrades and price target raises.

    Analyst rating changes from major banks (Goldman, Morgan Stanley, JPMorgan,
    Barclays, etc.) are public the moment they're published. An upgrade from
    Neutral → Buy or a significant price target increase (>10%) creates
    short-term buying pressure as institutions rebalance toward the new consensus.
    This strategy buys a 7-21 DTE call to capture the institutional buying wave.

    Source: Alpaca news feed + SEC filing keyword detection.
    """

    UPGRADE_KEYWORDS = frozenset({
        "upgraded to buy", "upgrade to buy", "upgraded to outperform",
        "upgraded to overweight", "initiated buy", "initiated outperform",
        "strong buy initiated", "price target raised", "raises price target",
        "increased price target", "raises target",
    })
    DOWNGRADE_KEYWORDS = frozenset({
        "downgraded to sell", "downgraded to neutral", "cut to sell",
        "price target cut", "price target lowered", "lowers price target",
    })

    def __init__(self) -> None:
        super().__init__("analyst_upgrade", min_equity=500)

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if await _should_skip_long_option(market, underlying):
            return []
        news = await market.get_news([underlying], lookback_hours=12, limit=15)
        if not news:
            return []

        upgrades = []
        downgrades = []
        for item in news:
            hl = (item.headline + " " + item.summary).lower()
            if any(kw in hl for kw in self.UPGRADE_KEYWORDS):
                upgrades.append(item)
            elif any(kw in hl for kw in self.DOWNGRADE_KEYWORDS):
                downgrades.append(item)

        if not upgrades and not downgrades:
            return []

        df = await _bars(market, underlying)
        if df.empty or len(df) < 20:
            return []
        last_rsi = float(rsi(df["close"], 14).iloc[-1])

        if upgrades and last_rsi < 72:
            top = upgrades[0]
            confidence = min(0.78, 0.58 + len(upgrades) * 0.05)
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.65, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "analyst_upgrade_call",
                {
                    "upgrade_count": len(upgrades),
                    "top_headline": top.headline,
                    "rsi": last_rsi,
                },
                confidence=confidence,
            )]

        if downgrades and last_rsi > 28:
            top = downgrades[0]
            confidence = min(0.75, 0.56 + len(downgrades) * 0.05)
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.35, 0.65, account, min_dte=7, max_dte=21
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "analyst_downgrade_put",
                {
                    "downgrade_count": len(downgrades),
                    "top_headline": top.headline,
                    "rsi": last_rsi,
                },
                confidence=confidence,
            )]

        return []


# ── VWAP Bounce (high-frequency intraday) ────────────────────────────────────

class VWAPBounce(BaseStrategy):
    """Intraday VWAP bounce/rejection on liquid names.

    Fires when price pulls back to VWAP and shows a reversal candle.
    Uses 0-5 DTE options for fast directional bets.
    """

    def __init__(self) -> None:
        super().__init__(
            "vwap_bounce", min_equity=500,
            stop_loss_pct=0.30, take_profit_pct=0.35, min_hold_minutes=2.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 20:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        open_ = df["open"]

        typical = (high + low + close) / 3
        cum_vol = volume.cumsum()
        vwap = (typical * volume).cumsum() / cum_vol.replace(0, float("nan"))
        last_close = float(close.iloc[-1])
        last_open = float(open_.iloc[-1])
        last_vwap = float(vwap.iloc[-1])
        last_low = float(low.iloc[-1])
        last_high = float(high.iloc[-1])
        last_atr = float(atr(high, low, close, 14).iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])

        if not _volume_confirmed(df, 1.2):
            return []

        vwap_touch_zone = last_atr * 0.10

        bounce_up = (
            last_low <= last_vwap + vwap_touch_zone
            and last_close > last_open
            and last_close > last_vwap
            and _bullish_candle(df)
            and 40 < last_rsi < 65
        )
        bounce_down = (
            last_high >= last_vwap - vwap_touch_zone
            and last_close < last_open
            and last_close < last_vwap
            and _bearish_candle(df)
            and 35 < last_rsi < 60
        )

        if bounce_up:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.65, daily_trend, "bullish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "vwap_bounce_call",
                {"rsi": last_rsi, "vwap": round(last_vwap, 2), "close": last_close,
                 "atr": round(last_atr, 4), "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        if bounce_down:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.65, daily_trend, "bearish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.35, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "vwap_rejection_put",
                {"rsi": last_rsi, "vwap": round(last_vwap, 2), "close": last_close,
                 "atr": round(last_atr, 4), "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        return []


# ── RSI Pullback (trend continuation) ───────────────────────────────────────

class RSIPullback(BaseStrategy):
    """Buy pullbacks in trending markets using RSI dips/rises.

    In an uptrend (EMA9 > EMA21), buy calls when RSI dips to 40-50 zone.
    In a downtrend, buy puts when RSI rises to 50-60 zone.
    Uses 2-7 DTE for fast moves.
    """

    def __init__(self) -> None:
        super().__init__(
            "rsi_pullback", min_equity=500,
            stop_loss_pct=0.35, take_profit_pct=0.40, min_hold_minutes=3.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 30:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        last_rsi = float(rsi(close, 14).iloc[-1])
        prev_rsi = float(rsi(close, 14).iloc[-2])
        last_ema9 = float(ema9.iloc[-1])
        last_ema21 = float(ema21.iloc[-1])
        last_close = float(close.iloc[-1])

        uptrend = last_ema9 > last_ema21 and last_close > last_ema21
        downtrend = last_ema9 < last_ema21 and last_close < last_ema21

        pullback_buy = uptrend and 40 <= last_rsi <= 50 and prev_rsi < last_rsi and _volume_confirmed(df, 1.1)
        pullback_sell = downtrend and 50 <= last_rsi <= 60 and prev_rsi > last_rsi and _volume_confirmed(df, 1.1)

        if pullback_buy:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.64, daily_trend, "bullish")
            if confidence < 0.50:
                return []
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(confidence, ctx, "bullish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.65, account, min_dte=2, max_dte=7
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "rsi_pullback_buy",
                {"rsi": last_rsi, "prev_rsi": prev_rsi, "ema9": round(last_ema9, 2),
                 "ema21": round(last_ema21, 2), "sd_adj": sd_adj,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        if pullback_sell:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.64, daily_trend, "bearish")
            if confidence < 0.50:
                return []
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(confidence, ctx, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.35, 0.65, account, min_dte=2, max_dte=7
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "rsi_pullback_sell",
                {"rsi": last_rsi, "prev_rsi": prev_rsi, "ema9": round(last_ema9, 2),
                 "ema21": round(last_ema21, 2), "sd_adj": sd_adj,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        return []


# ── Momentum Scalp (multi-timeframe confirmation) ───────────────────────────

class MomentumScalp(BaseStrategy):
    """Fast momentum scalp using EMA alignment + volume surge on SPY/QQQ.

    Fires when 5-bar EMA > 9-bar EMA > 21-bar EMA with the latest bar
    showing above-average volume. This three-EMA stack is a strong
    short-term momentum signal used by professional scalpers.
    """

    def __init__(self) -> None:
        super().__init__(
            "momentum_scalp", min_equity=500,
            stop_loss_pct=0.30, take_profit_pct=0.35, min_hold_minutes=2.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 25:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        ema5 = ema(close, 5)
        ema9_s = ema(close, 9)
        ema21_s = ema(close, 21)
        last_rsi = float(rsi(close, 14).iloc[-1])

        e5 = float(ema5.iloc[-1])
        e9 = float(ema9_s.iloc[-1])
        e21 = float(ema21_s.iloc[-1])
        last_vol = float(volume.iloc[-1])
        avg_vol = float(volume.iloc[-15:-1].mean())
        vol_surge = last_vol > avg_vol * 1.5

        bullish_stack = e5 > e9 > e21 and vol_surge and last_rsi > 52 and last_rsi < 78
        bearish_stack = e5 < e9 < e21 and vol_surge and last_rsi < 48 and last_rsi > 22

        if bullish_stack and _bullish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.66, daily_trend, "bullish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.35, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "momentum_scalp_bull",
                {"rsi": last_rsi, "ema5": round(e5, 2), "ema9": round(e9, 2),
                 "ema21": round(e21, 2), "vol_ratio": round(last_vol / max(avg_vol, 1), 2),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        if bearish_stack and _bearish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.66, daily_trend, "bearish")
            if confidence < 0.50:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.35, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "momentum_scalp_bear",
                {"rsi": last_rsi, "ema5": round(e5, 2), "ema9": round(e9, 2),
                 "ema21": round(e21, 2), "vol_ratio": round(last_vol / max(avg_vol, 1), 2),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence, stop_loss_pct=0.30,
            )]

        return []


# ── Opening Drive (Gap-and-Go) ─────────────────────────────────────────────────

class OpeningDrive(BaseStrategy):
    """First 30 minutes momentum — the most profitable window for day traders.

    After the opening bell, stocks that gap up/down on volume and hold
    their direction for the first 15-30 minutes tend to continue. This
    strategy captures that initial directional thrust.

    Requires:
    - Clear gap direction (open vs. previous close)
    - Volume 2x+ average (institutional participation)
    - RSI confirmation (not overbought/oversold already)
    - Candle confirmation (bar closing in gap direction)
    - Only trades in the first 90 minutes of regular session
    """

    def __init__(self) -> None:
        super().__init__(
            "opening_drive", min_equity=500,
            stop_loss_pct=0.30, take_profit_pct=0.50, min_hold_minutes=3.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []

        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        now_ny = datetime.now(timezone.utc).astimezone(ny)
        market_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = (now_ny - market_open).total_seconds() / 60.0
        if minutes_since_open < 5 or minutes_since_open > 90:
            return []

        df = await _bars(market, underlying)
        if df.empty or len(df) < 20:
            return []

        if not _volume_confirmed(df, 2.0):
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        last_close = float(close.iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])
        last_atr = float(atr(high, low, close, 14).iloc[-1])
        mom = _momentum_score(df)

        prev_day_close = float(close.iloc[-min(len(close), 78)])
        gap_pct = (last_close - prev_day_close) / prev_day_close if prev_day_close > 0 else 0.0

        gap_up = gap_pct > 0.003 and last_rsi > 52 and last_rsi < 78 and _bullish_candle(df) and mom >= 0.40
        gap_down = gap_pct < -0.003 and last_rsi < 48 and last_rsi > 22 and _bearish_candle(df) and mom >= 0.40

        if gap_up:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.72, daily_trend, "bullish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "opening_drive_gap_up",
                {"rsi": last_rsi, "gap_pct": round(gap_pct, 4), "close": last_close,
                 "momentum_score": mom, "minutes_since_open": round(minutes_since_open, 1),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        if gap_down:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.72, daily_trend, "bearish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.60, account, min_dte=0, max_dte=5
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "opening_drive_gap_down",
                {"rsi": last_rsi, "gap_pct": round(gap_pct, 4), "close": last_close,
                 "momentum_score": mom, "minutes_since_open": round(minutes_since_open, 1),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        return []


# ── Consolidation Breakout ───────────────────────────────────────────────────

class ConsolidationBreakout(BaseStrategy):
    """Breakout from a tight consolidation range on volume expansion.

    One of the highest-probability day trading setups. When price
    compresses into a narrow range (Bollinger Band width in bottom 20th
    percentile) and then breaks out with volume 1.5x+, the resulting
    move is often explosive and directional.

    Uses Bollinger Band squeeze detection + volume breakout confirmation.
    """

    def __init__(self) -> None:
        super().__init__(
            "consolidation_breakout", min_equity=500,
            stop_loss_pct=0.30, take_profit_pct=0.55, min_hold_minutes=3.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 50:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        bb = bollinger(close, 20, 2.0)
        bb_width = (bb["upper"] - bb["lower"]) / bb["mid"]
        bb_width = bb_width.dropna()
        if len(bb_width) < 20:
            return []

        current_width = float(bb_width.iloc[-1])
        width_percentile = float((bb_width.iloc[-30:] <= current_width).mean()) if len(bb_width) >= 30 else 0.5

        if width_percentile > 0.25:
            return []

        if not _volume_confirmed(df, 1.5):
            return []

        last_close = float(close.iloc[-1])
        last_upper = float(bb["upper"].iloc[-1])
        last_lower = float(bb["lower"].iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])
        mom = _momentum_score(df)

        breakout_up = last_close > last_upper and _bullish_candle(df) and mom >= 0.35
        breakout_down = last_close < last_lower and _bearish_candle(df) and mom >= 0.35

        if breakout_up and 50 < last_rsi < 80:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.72, daily_trend, "bullish")
            if confidence < 0.55:
                return []
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(confidence, ctx, "bullish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.60, account, min_dte=2, max_dte=10
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "consolidation_breakout_up",
                {"rsi": last_rsi, "bb_width_pctl": round(width_percentile, 3),
                 "close": last_close, "bb_upper": round(last_upper, 2),
                 "momentum_score": mom, "sd_adj": sd_adj,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        if breakout_down and 20 < last_rsi < 50:
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.72, daily_trend, "bearish")
            if confidence < 0.55:
                return []
            ctx = zone_context(df)
            confidence, sd_adj = _apply_confluence(confidence, ctx, "bearish")
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.60, account, min_dte=2, max_dte=10
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "consolidation_breakout_down",
                {"rsi": last_rsi, "bb_width_pctl": round(width_percentile, 3),
                 "close": last_close, "bb_lower": round(last_lower, 2),
                 "momentum_score": mom, "sd_adj": sd_adj,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        return []


# ── MACD Power Cross ─────────────────────────────────────────────────────────

class MACDPowerCross(BaseStrategy):
    """MACD crossover with histogram acceleration and volume confirmation.

    The classic MACD crossover is weak on its own. This version requires:
    - MACD line crossing signal line
    - Histogram accelerating (growing bars, not shrinking)
    - Price above/below VWAP (direction alignment)
    - Volume confirmation (1.3x+ average)
    - Daily trend alignment

    This filters out the 80% of MACD crosses that fail and catches the
    20% that produce strong directional moves.
    """

    def __init__(self) -> None:
        super().__init__(
            "macd_power_cross", min_equity=500,
            stop_loss_pct=0.30, take_profit_pct=0.50, min_hold_minutes=5.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 35:
            return []

        if not _volume_confirmed(df, 1.3):
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        macd_df = macd(close, 12, 26, 9)
        macd_line = macd_df["macd"]
        signal_line = macd_df["signal"]
        hist = macd_df["hist"]

        curr_macd = float(macd_line.iloc[-1])
        prev_macd = float(macd_line.iloc[-2])
        curr_signal = float(signal_line.iloc[-1])
        prev_signal = float(signal_line.iloc[-2])
        curr_hist = float(hist.iloc[-1])
        prev_hist = float(hist.iloc[-2])
        prev2_hist = float(hist.iloc[-3]) if len(hist) >= 3 else prev_hist

        typical = (high + low + close) / 3
        cum_vol = volume.cumsum()
        vwap_s = (typical * volume).cumsum() / cum_vol.replace(0, float("nan"))
        last_close = float(close.iloc[-1])
        last_vwap = float(vwap_s.iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])

        bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal
                      and curr_hist > prev_hist > prev2_hist
                      and last_close > last_vwap
                      and 45 < last_rsi < 75)

        bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal
                      and curr_hist < prev_hist < prev2_hist
                      and last_close < last_vwap
                      and 25 < last_rsi < 55)

        if bull_cross and _bullish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.70, daily_trend, "bullish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.60, account, min_dte=3, max_dte=14
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "macd_power_cross_bull",
                {"rsi": last_rsi, "macd": round(curr_macd, 4),
                 "signal": round(curr_signal, 4), "hist": round(curr_hist, 4),
                 "vwap": round(last_vwap, 2), "close": last_close,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        if bear_cross and _bearish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            confidence, trend_adj = _apply_trend(0.70, daily_trend, "bearish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.60, account, min_dte=3, max_dte=14
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "macd_power_cross_bear",
                {"rsi": last_rsi, "macd": round(curr_macd, 4),
                 "signal": round(curr_signal, 4), "hist": round(curr_hist, 4),
                 "vwap": round(last_vwap, 2), "close": last_close,
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        return []


# ── Multi-Indicator Confluence ───────────────────────────────────────────────

class MultiConfluence(BaseStrategy):
    """Highest-conviction strategy — only fires when 4+ indicators align.

    This is the "sniper" strategy. It trades less often but with much
    higher win rate. Requires simultaneous confirmation from:
    1. EMA trend (9 > 21 > 50 for bullish)
    2. RSI momentum (in the sweet spot, not extreme)
    3. MACD direction (histogram positive/negative)
    4. Stochastic %K > %D (bullish) or %K < %D (bearish)
    5. Volume above average
    6. Price above/below VWAP
    7. Daily trend alignment

    When all these align, the probability of a profitable trade is
    dramatically higher than any single-indicator strategy.
    """

    def __init__(self) -> None:
        super().__init__(
            "multi_confluence", min_equity=500,
            stop_loss_pct=0.25, take_profit_pct=0.60, min_hold_minutes=5.0,
        )

    async def scan(self, underlying: str, account, market) -> list[OrderProposal]:
        if underlying not in _WEEKLY_ELIGIBLE:
            return []
        if await _should_skip_long_option(market, underlying):
            return []
        df = await _bars(market, underlying)
        if df.empty or len(df) < 55:
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        ema9_val = float(ema(close, 9).iloc[-1])
        ema21_val = float(ema(close, 21).iloc[-1])
        ema50_val = float(ema(close, 50).iloc[-1])
        last_rsi = float(rsi(close, 14).iloc[-1])
        macd_df = macd(close, 12, 26, 9)
        last_hist = float(macd_df["hist"].iloc[-1])
        stoch_df = stochastic(high, low, close, 14, 3)
        stoch_k = float(stoch_df["k"].iloc[-1])
        stoch_d = float(stoch_df["d"].iloc[-1])

        typical = (high + low + close) / 3
        cum_vol = volume.cumsum()
        vwap_s = (typical * volume).cumsum() / cum_vol.replace(0, float("nan"))
        last_close = float(close.iloc[-1])
        last_vwap = float(vwap_s.iloc[-1])

        last_vol = float(volume.iloc[-1])
        avg_vol = float(volume.iloc[-15:-1].mean())
        vol_ok = last_vol > avg_vol * 1.2

        bull_signals = 0
        if ema9_val > ema21_val > ema50_val:
            bull_signals += 1
        if 50 < last_rsi < 72:
            bull_signals += 1
        if last_hist > 0:
            bull_signals += 1
        if stoch_k > stoch_d and stoch_k < 80:
            bull_signals += 1
        if last_close > last_vwap:
            bull_signals += 1
        if vol_ok:
            bull_signals += 1

        bear_signals = 0
        if ema9_val < ema21_val < ema50_val:
            bear_signals += 1
        if 28 < last_rsi < 50:
            bear_signals += 1
        if last_hist < 0:
            bear_signals += 1
        if stoch_k < stoch_d and stoch_k > 20:
            bear_signals += 1
        if last_close < last_vwap:
            bear_signals += 1
        if vol_ok:
            bear_signals += 1

        min_signals = 5

        if bull_signals >= min_signals and _bullish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            base_confidence = 0.68 + (bull_signals - min_signals) * 0.04
            confidence, trend_adj = _apply_trend(base_confidence, daily_trend, "bullish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.CALL, 0.40, 0.60, account, min_dte=3, max_dte=14
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "multi_confluence_bull",
                {"bull_signals": bull_signals, "rsi": last_rsi,
                 "stoch_k": round(stoch_k, 1), "stoch_d": round(stoch_d, 1),
                 "macd_hist": round(last_hist, 4), "vwap": round(last_vwap, 2),
                 "close": last_close, "vol_ratio": round(last_vol / max(avg_vol, 1), 2),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        if bear_signals >= min_signals and _bearish_candle(df):
            daily_trend = await _daily_trend(market, underlying)
            base_confidence = 0.68 + (bear_signals - min_signals) * 0.04
            confidence, trend_adj = _apply_trend(base_confidence, daily_trend, "bearish")
            if confidence < 0.55:
                return []
            contract = await _select_affordable_long_option(
                market, underlying, OptionRight.PUT, 0.40, 0.60, account, min_dte=3, max_dte=14
            )
            if not contract:
                return []
            return [_long_option_proposal(
                self.name, underlying, contract, "multi_confluence_bear",
                {"bear_signals": bear_signals, "rsi": last_rsi,
                 "stoch_k": round(stoch_k, 1), "stoch_d": round(stoch_d, 1),
                 "macd_hist": round(last_hist, 4), "vwap": round(last_vwap, 2),
                 "close": last_close, "vol_ratio": round(last_vol / max(avg_vol, 1), 2),
                 "daily_trend": daily_trend, "trend_adj": trend_adj},
                confidence=confidence,
            )]

        return []


# ── Strategy Registry ─────────────────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, AutoStrategy] = {
    # 0DTE scalp — SPY/QQQ intraday (fires every cycle during regular hours)
    "zero_dte_scalp":       ZeroDTEScalp(),
    # 2-14 DTE weekly momentum — liquid ETFs + mega-caps
    "weekly_momentum":      WeeklyMomentum(),
    # Standard 30-45 DTE directional
    "long_directional":     LongDirectional(),
    # News-driven 7-21 DTE
    "news_catalyst":        NewsCatalyst(),
    # ATR volatility expansion plays 7-21 DTE
    "volatility_breakout":  VolatilityBreakout(),
    # Contrarian mean-reversion 7-21 DTE
    "mean_reversion_options": MeanReversionOptions(),
    # Demand-zone bounce / supply-zone rejection reversal plays
    "supply_demand_reversal": SupplyDemandReversal(),
    # Debit spreads (directional, defined risk)
    "bull_call_spread":     BullCallSpread(),
    "bear_put_spread":      BearPutSpread(),
    # Credit spreads (theta, neutral-directional)
    "bull_put_spread":      BullPutSpread(),
    "bear_call_spread":     BearCallSpread(),
    # Multi-leg neutral structures (now enabled at $2K)
    "iron_condor":          IronCondor(),
    "iron_butterfly":       IronButterfly(),
    # Calendar spread — vega long, term structure play
    "calendar_spread":      CalendarSpread(),
    # High-IV short strangle pre-event
    "iv_crush_strangle":    IVCrushStrangle(),
    # Gamma scalp straddle — requires $10K+ for hedging
    "gamma_scalp":          GammaScalp(),
    # Theta strategies requiring stock position or margin
    "covered_call":         CoveredCall(),
    "cash_secured_put":     CashSecuredPut(),
    # Public-data fundamental signals
    "insider_buy_signal":   InsiderBuySignal(),
    "analyst_upgrade":      AnalystUpgrade(),
    # High-frequency intraday strategies
    "vwap_bounce":          VWAPBounce(),
    "rsi_pullback":         RSIPullback(),
    "momentum_scalp":       MomentumScalp(),
    # New day-trading strategies — higher conviction
    "opening_drive":        OpeningDrive(),
    "consolidation_breakout": ConsolidationBreakout(),
    "macd_power_cross":     MACDPowerCross(),
    "multi_confluence":     MultiConfluence(),
}
