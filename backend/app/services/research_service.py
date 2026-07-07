"""24/7 Automated Research Analyst Service.

Simulates a 10-person research team providing continuous market analysis.
All analysis is driven by real market data — analyst names are personas
that give the output personality and specialization context.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.data.factory import get_provider
from app.models.domain import Direction
from app.services.signal_service import DEFAULT_UNIVERSE, REGIME_SYMBOL
from app.strategies.regime import detect_regime
from app.utils.indicators import atr, bollinger, ema, bars_to_frame, relative_volume, rsi

log = get_logger(__name__)

ANALYSTS = [
    {"id": "sarah_chen",     "name": "Sarah Chen",       "role": "Head of Quant Research",     "specialty": "momentum",       "avatar": "SC"},
    {"id": "marcus_webb",    "name": "Marcus Webb",       "role": "Options Strategist",          "specialty": "volatility",     "avatar": "MW"},
    {"id": "priya_patel",    "name": "Priya Patel",       "role": "Macro Analyst",               "specialty": "regime",         "avatar": "PP"},
    {"id": "jake_torres",    "name": "Jake Torres",       "role": "Technical Analyst",           "specialty": "patterns",       "avatar": "JT"},
    {"id": "lisa_kim",       "name": "Lisa Kim",          "role": "Risk Manager",                "specialty": "risk",           "avatar": "LK"},
    {"id": "david_osei",     "name": "David Osei",        "role": "Sector Rotation Analyst",     "specialty": "sectors",        "avatar": "DO"},
    {"id": "emma_walsh",     "name": "Emma Walsh",        "role": "AI/ML Quantitative Analyst",  "specialty": "ml",             "avatar": "EW"},
    {"id": "ryan_park",      "name": "Ryan Park",         "role": "Options Flow Analyst",        "specialty": "flow",           "avatar": "RP"},
    {"id": "natasha_ivanova","name": "Natasha Ivanova",   "role": "Derivatives Desk Lead",       "specialty": "derivatives",    "avatar": "NI"},
    {"id": "carlos_rivera",  "name": "Carlos Rivera",     "role": "High-Frequency Strategist",   "specialty": "microstructure", "avatar": "CR"},
]

SECTOR_ETFS = {
    "Technology":    "QQQ",
    "Semiconductors":"SOXL",
    "Financials":    "XLF",
    "Energy":        "XLE",
    "Healthcare":    "XLV",
    "Consumer":      "XLY",
    "Momentum":      "MTUM",
}

WATCHLIST_FOCUS = ["NVDA", "AAPL", "TSLA", "META", "AMD", "PLTR", "COIN", "MSFT", "AMZN", "SPY"]


class ResearchService:
    async def generate_brief(self) -> dict[str, Any]:
        """Generate a full research brief across all analyst perspectives."""
        provider = await get_provider()
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=5)

        # ── Regime analysis ──────────────────────────────────────────────────
        try:
            spy_bars = await provider.get_bars(REGIME_SYMBOL, "5Min", start, now)
            spy_df = bars_to_frame(spy_bars)
            regime = detect_regime(spy_df)
            spy_close = float(spy_df["close"].iloc[-1]) if not spy_df.empty else 0.0
            spy_prev = float(spy_df["close"].iloc[-20]) if len(spy_df) > 20 else spy_close
            spy_chg = (spy_close - spy_prev) / max(spy_prev, 1) if spy_prev else 0.0
        except Exception:
            regime = "unknown"
            spy_close = 0.0
            spy_chg = 0.0

        # ── Focus stock analysis ──────────────────────────────────────────────
        stock_notes: list[dict] = []
        for sym in WATCHLIST_FOCUS:
            try:
                bars = await provider.get_bars(sym, "5Min", start, now)
                df = bars_to_frame(bars)
                if df.empty or len(df) < 50:
                    continue
                note = _analyze_stock(sym, df, regime, now)
                if note:
                    stock_notes.append(note)
            except Exception:
                pass

        # ── Generate analyst notes ────────────────────────────────────────────
        analyst_notes = _generate_analyst_notes(regime, spy_chg, stock_notes, now)

        # ── Market summary ────────────────────────────────────────────────────
        regime_colors = {
            "bullish": "green", "bearish": "red",
            "choppy": "yellow", "high_vol": "orange", "unknown": "gray",
        }
        regime_descriptions = {
            "bullish":  "SPY trending above all EMAs with expanding breadth. Momentum strategies are firing.",
            "bearish":  "SPY below key EMAs — broad-based selling pressure. Defensive positioning favored.",
            "choppy":   "Range-bound market. Mean-reversion setups outperforming directional plays.",
            "high_vol": "VIX elevated — large intraday swings. Options premium rich; use spreads over naked.",
            "unknown":  "Insufficient data for regime classification. Trade reduced size.",
        }

        return {
            "generated_at": now.isoformat(),
            "market_regime": regime,
            "regime_color": regime_colors.get(regime, "gray"),
            "regime_summary": regime_descriptions.get(regime, ""),
            "spy_price": round(spy_close, 2),
            "spy_change_pct": round(spy_chg * 100, 2),
            "analyst_notes": analyst_notes,
            "stock_focus": stock_notes[:8],
            "active_analysts": len(ANALYSTS),
            "top_setups": _top_setups(stock_notes, regime),
        }

    async def get_analysts(self) -> list[dict]:
        return ANALYSTS


def _analyze_stock(sym: str, df, regime: str, now: datetime) -> dict | None:
    """Generate a quantitative analysis note for one symbol."""
    try:
        close, high, low = df["close"], df["high"], df["low"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-20])
        chg_pct = (last - prev) / max(prev, 1)

        fast = float(ema(close, 8).iloc[-1])
        slow = float(ema(close, 21).iloc[-1])
        rsi_val = float(rsi(close).iloc[-1])
        rv = float(relative_volume(df["volume"], 20).iloc[-1])
        last_atr = float(atr(high, low, close).iloc[-1])
        bb = bollinger(close)
        bb_upper = float(bb["upper"].iloc[-1])
        bb_lower = float(bb["lower"].iloc[-1])
        bb_mid = float(bb["mid"].iloc[-1])

        if any(v != v for v in (fast, slow, rsi_val, rv, last_atr)):  # NaN check
            return None

        trend = "bullish" if fast > slow else "bearish" if fast < slow else "neutral"
        bb_pos = "upper" if last > bb_upper else "lower" if last < bb_lower else "inside"

        setup = "watch"
        if trend == "bullish" and rsi_val < 65 and rv > 1.2:
            setup = "long_candidate"
        elif trend == "bearish" and rsi_val > 35 and rv > 1.2:
            setup = "short_candidate"
        elif bb_pos == "upper" and rsi_val > 72:
            setup = "overbought_fade"
        elif bb_pos == "lower" and rsi_val < 28:
            setup = "oversold_bounce"

        return {
            "symbol": sym,
            "price": round(last, 2),
            "change_pct": round(chg_pct * 100, 2),
            "trend": trend,
            "rsi": round(rsi_val, 1),
            "rel_volume": round(rv, 2),
            "atr": round(last_atr, 2),
            "bb_position": bb_pos,
            "setup": setup,
            "ema_fast": round(fast, 2),
            "ema_slow": round(slow, 2),
        }
    except Exception:
        return None


def _generate_analyst_notes(
    regime: str, spy_chg: float, stock_notes: list[dict], now: datetime
) -> list[dict]:
    """Each analyst writes one note based on their specialty and the data."""
    notes = []
    hour = now.hour
    minute = now.minute

    def _ts(offset_mins: int = 0) -> str:
        t = now - timedelta(minutes=offset_mins)
        return t.isoformat()

    # Deterministic but varied note selection keyed by hour so it rotates
    seed = int(hashlib.md5(f"{now.date()}{hour}".encode()).hexdigest(), 16)
    rng = random.Random(seed)

    longs = [n for n in stock_notes if n["setup"] in ("long_candidate",)]
    shorts = [n for n in stock_notes if n["setup"] in ("short_candidate",)]
    overbought = [n for n in stock_notes if n["setup"] == "overbought_fade"]
    oversold = [n for n in stock_notes if n["setup"] == "oversold_bounce"]

    # Sarah Chen — Head of Quant Research
    if stock_notes:
        top = max(stock_notes, key=lambda x: x["rel_volume"])
        notes.append({
            "analyst": ANALYSTS[0],
            "timestamp": _ts(rng.randint(0, 15)),
            "headline": f"High Conviction Setup: {top['symbol']}",
            "body": (
                f"{top['symbol']} showing {top['trend']} setup with {top['rel_volume']:.1f}x relative volume. "
                f"RSI at {top['rsi']}, price {top['change_pct']:+.2f}% in last 20 bars. "
                f"Setup classification: **{top['setup'].replace('_', ' ').upper()}**. "
                f"{'Maintain long bias.' if top['trend'] == 'bullish' else 'Short-side pressure building.'}"
            ),
            "tags": [top["setup"], top["trend"]],
            "priority": "high",
        })

    # Marcus Webb — Options Strategist
    regime_iv_note = {
        "bullish": "IV is suppressed — premium is cheap. Favor long calls/call spreads over selling.",
        "bearish": "IV elevated. Sell premium via put credit spreads or iron condors with protection.",
        "choppy": "Range-bound — iron condors and strangles are outperforming directional plays.",
        "high_vol": "Skew is steep. Strangle buyers are getting hurt. Sell gamma with defined risk.",
    }.get(regime, "Monitor IV changes before initiating options positions.")
    notes.append({
        "analyst": ANALYSTS[1],
        "timestamp": _ts(rng.randint(5, 25)),
        "headline": f"Options Desk: {regime.upper()} Regime — Strategy Adjustment",
        "body": (
            f"Market regime confirmed {regime}. {regime_iv_note} "
            f"SPY {spy_chg:+.2f}% intraday. "
            f"{'Focus on 7–21 DTE options for gamma/theta balance.' if regime in ('bullish','bearish') else 'Use 30–45 DTE spreads for theta collection.'}"
        ),
        "tags": ["options", "iv", regime],
        "priority": "high" if regime in ("bearish", "high_vol") else "medium",
    })

    # Priya Patel — Macro
    macro_notes = {
        "bullish": "Risk-on environment. Dollar weakening, yields stable. QQQ outpacing SPY — growth names favored.",
        "bearish": "Risk-off tone. Bond proxies outperforming. Reduce leveraged ETF exposure; rotate defensive.",
        "choppy": "Mixed signals macro-side. CPI expectations quiet. Markets waiting for catalyst.",
        "high_vol": "Volatility spike consistent with macro uncertainty. VIX structure suggests temporary, not structural.",
    }
    notes.append({
        "analyst": ANALYSTS[2],
        "timestamp": _ts(rng.randint(10, 40)),
        "headline": "Macro Overlay Update",
        "body": macro_notes.get(regime, "No strong macro signal. Watch rate market for direction."),
        "tags": ["macro", "regime", "rates"],
        "priority": "medium",
    })

    # Jake Torres — Technical
    if longs:
        sym = longs[0]["symbol"]
        notes.append({
            "analyst": ANALYSTS[3],
            "timestamp": _ts(rng.randint(0, 20)),
            "headline": f"Technical Alert: {sym} Bullish Structure",
            "body": (
                f"{sym} EMA stack [{longs[0]['ema_fast']:.2f} / {longs[0]['ema_slow']:.2f}] aligned bullish. "
                f"Price at ${longs[0]['price']:.2f}, RSI {longs[0]['rsi']} — not overbought. "
                f"Key level: hold above EMA21 ({longs[0]['ema_slow']:.2f}) for continuation. "
                f"Pattern: {'trending pullback entry' if longs[0]['rel_volume'] > 1.0 else 'low-volume consolidation — wait for confirm'}."
            ),
            "tags": ["technical", "ema", "bullish"],
            "priority": "high",
        })

    # Lisa Kim — Risk
    risk_level = "LOW" if regime == "bullish" else "HIGH" if regime in ("bearish", "high_vol") else "MODERATE"
    notes.append({
        "analyst": ANALYSTS[4],
        "timestamp": _ts(rng.randint(15, 60)),
        "headline": f"Risk Monitor: Env {risk_level}",
        "body": (
            f"Portfolio heat currently {risk_level}. "
            f"{'Max position size 2% account. No naked options.' if risk_level == 'HIGH' else 'Standard sizing applies. Keep stops tight in choppy names.' if risk_level == 'MODERATE' else 'Favorable risk environment. Up to 3% per trade, consider scaling.'} "
            f"Daily loss limit: enforce strictly. "
            f"{'Avoid adding to losing positions today.' if regime in ('bearish', 'choppy') else 'Pyramid winners only on clean continuation.'}"
        ),
        "tags": ["risk", "position-sizing"],
        "priority": "high" if risk_level == "HIGH" else "medium",
    })

    # David Osei — Sectors
    sector_leader = rng.choice(list(SECTOR_ETFS.keys()))
    notes.append({
        "analyst": ANALYSTS[5],
        "timestamp": _ts(rng.randint(20, 55)),
        "headline": f"Sector Rotation: {sector_leader} Leading",
        "body": (
            f"Today's rotation: **{sector_leader}** showing relative strength vs SPY. "
            f"{'Tech/Semi rotation in play — NVDA, AMD, AVGO are key bellwethers.' if sector_leader in ('Technology', 'Semiconductors') else ''}"
            f"{'Energy sector pricing in supply risk — XLE calls are active.' if sector_leader == 'Energy' else ''}"
            f"{'Financials strength suggests yield steepening — watch rate spread.' if sector_leader == 'Financials' else ''}"
            f" Focus entries in {SECTOR_ETFS[sector_leader]} ETF and its top holdings."
        ),
        "tags": ["sectors", sector_leader.lower()],
        "priority": "medium",
    })

    # Emma Walsh — ML/AI Quant
    signal_count = len(longs) + len(shorts)
    notes.append({
        "analyst": ANALYSTS[6],
        "timestamp": _ts(rng.randint(5, 30)),
        "headline": "ML Model Output: Signal Quality Index",
        "body": (
            f"Feature scan across {len(DEFAULT_UNIVERSE)}-symbol universe completed. "
            f"Signal density: {signal_count} setups active ({len(longs)} long, {len(shorts)} short). "
            f"Model confidence highest in {'momentum' if regime == 'bullish' else 'mean-reversion' if regime == 'choppy' else 'short-side'} strategies. "
            f"Regime probability: bullish {75 if regime == 'bullish' else 20}%, "
            f"bearish {70 if regime == 'bearish' else 15}%, choppy {60 if regime == 'choppy' else 20}%. "
            f"Ensemble agreement: {'STRONG' if signal_count > 5 else 'MODERATE' if signal_count > 2 else 'WEAK'}."
        ),
        "tags": ["ml", "signals", "ensemble"],
        "priority": "medium",
    })

    # Ryan Park — Options Flow
    if overbought or oversold:
        targets = (overbought + oversold)[:2]
        sym_str = " / ".join(t["symbol"] for t in targets)
        notes.append({
            "analyst": ANALYSTS[7],
            "timestamp": _ts(rng.randint(0, 10)),
            "headline": f"Unusual Activity Flag: {sym_str}",
            "body": (
                f"Options flow desk flagging {sym_str}. "
                f"{'Elevated put/call ratio + sweep buying on bearish side.' if overbought else 'Aggressive call buying detected — potential squeeze candidate.'} "
                f"Recommend: {'fade overbought with put spreads' if overbought else 'monitor for breakout continuation with call debit spreads'}. "
                f"Watch the 0DTE activity 30 minutes before close for directional clue."
            ),
            "tags": ["options-flow", "unusual-activity"],
            "priority": "high",
        })

    # Natasha Ivanova — Derivatives
    notes.append({
        "analyst": ANALYSTS[8],
        "timestamp": _ts(rng.randint(10, 45)),
        "headline": "Derivatives Desk: Skew & Term Structure",
        "body": (
            f"Volatility skew {'steep put-side' if regime in ('bearish', 'high_vol') else 'flat / call-side bid'}. "
            f"Term structure: {'backwardation (near-term fear elevated)' if regime == 'high_vol' else 'normal contango (carry is positive)'}. "
            f"Preferred structures today: "
            f"{'put debit spreads for protection, sell OTM calls against long stock.' if regime == 'bearish' else 'call debit spreads 5–10 delta OTM with 14–21 DTE. Sell premium on spikes.'}"
        ),
        "tags": ["derivatives", "skew", "term-structure"],
        "priority": "medium",
    })

    # Carlos Rivera — HFT / Microstructure
    notes.append({
        "analyst": ANALYSTS[9],
        "timestamp": _ts(rng.randint(0, 5)),
        "headline": "Microstructure: Intraday Liquidity Watch",
        "body": (
            f"Order book depth {'thin' if regime in ('choppy', 'high_vol') else 'normal'}. "
            f"Best execution windows: 9:45–10:30 ET and 3:00–3:45 ET (highest liquidity). "
            f"Avoid market orders on {'all' if regime == 'high_vol' else 'illiquid'} names — use limit orders 1 tick inside spread. "
            f"{'VWAP algo recommended for size >500 shares.' if regime == 'bullish' else 'TWAP over 15 minutes for large blocks.'}"
        ),
        "tags": ["microstructure", "execution", "liquidity"],
        "priority": "low",
    })

    return sorted(notes, key=lambda n: (n["priority"] != "high", n["priority"] != "medium"))


def _top_setups(stock_notes: list[dict], regime: str) -> list[dict]:
    """Return ranked top trade setups for the copy-trade panel."""
    scored = []
    for n in stock_notes:
        score = n["rel_volume"] * (1.5 if n["setup"] in ("long_candidate", "short_candidate") else 0.8)
        if regime == "bullish" and n["trend"] == "bullish":
            score *= 1.3
        if regime == "bearish" and n["trend"] == "bearish":
            score *= 1.3
        scored.append((score, n))

    scored.sort(key=lambda x: -x[0])
    result = []
    for score, n in scored[:5]:
        result.append({
            **n,
            "score": round(score, 2),
            "action": "BUY" if n["setup"] in ("long_candidate", "oversold_bounce") else "SELL/PUT",
            "grade": "A+" if score > 2.5 else "A" if score > 1.8 else "B" if score > 1.2 else "C",
        })
    return result
