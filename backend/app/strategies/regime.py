"""Market Regime detection.

Looks at SPY (or configured index) to classify the broad tape:
 bullish | bearish | choppy | high_vol | unknown

Used as context for other strategies — bonus/penalty to confidence.
"""
from __future__ import annotations

import numpy as np

from app.utils.indicators import atr, ema


def detect_regime(spy_df) -> str:
    if spy_df is None or len(spy_df) < 50:
        return "unknown"

    close = spy_df["close"]
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    last = close.iloc[-1]

    a = atr(spy_df["high"], spy_df["low"], spy_df["close"], 14).iloc[-1]
    atr_pct = (a / last) if last else 0
    realized_vol = np.std(close.pct_change().dropna().tail(20)) * np.sqrt(252)

    if realized_vol > 0.35 or atr_pct > 0.025:
        return "high_vol"
    if last > ema20 > ema50:
        return "bullish"
    if last < ema20 < ema50:
        return "bearish"
    return "choppy"
