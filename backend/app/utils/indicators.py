"""Technical indicators. Pure functions, no I/O, easy to test."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return pd.DataFrame({"macd": macd_line, "signal": sig_line, "hist": hist})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def bollinger(series: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    m = sma(series, period)
    s = series.rolling(period, min_periods=period).std()
    return pd.DataFrame({"mid": m, "upper": m + k * s, "lower": m - k * s})


def vwap(df: pd.DataFrame) -> pd.Series:
    """Requires columns: high, low, close, volume."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    return (tp * df["volume"]).cumsum() / cum_vol.replace(0, np.nan)


def relative_volume(volume: pd.Series, lookback: int = 20) -> pd.Series:
    avg = volume.rolling(lookback, min_periods=lookback).mean()
    return volume / avg


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3
) -> pd.DataFrame:
    """Stochastic %K and %D oscillator."""
    lowest = low.rolling(k, min_periods=k).min()
    highest = high.rolling(k, min_periods=k).max()
    pct_k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    pct_d = pct_k.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"k": pct_k, "d": pct_d})


def keltner(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    ema_period: int = 20,
    atr_period: int = 14,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Keltner Channel: EMA ± multiplier * ATR."""
    mid = ema(close, ema_period)
    band = atr(high, low, close, atr_period) * multiplier
    return pd.DataFrame({"upper": mid + band, "mid": mid, "lower": mid - band})


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend indicator. Returns direction (+1 bull, -1 bear) and line."""
    hl2 = (high + low) / 2
    a = atr(high, low, close, period)
    upper_basic = hl2 + multiplier * a
    lower_basic = hl2 - multiplier * a

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()
    direction = pd.Series(np.ones(len(close)), index=close.index)
    st = pd.Series(np.zeros(len(close)), index=close.index)

    for i in range(1, len(close)):
        ub_prev = upper_band.iloc[i - 1]
        lb_prev = lower_band.iloc[i - 1]
        ub_curr = upper_basic.iloc[i]
        lb_curr = lower_basic.iloc[i]
        c_prev = float(close.iloc[i - 1])
        c_curr = float(close.iloc[i])

        upper_band.iloc[i] = ub_curr if ub_curr < ub_prev or c_prev > ub_prev else ub_prev
        lower_band.iloc[i] = lb_curr if lb_curr > lb_prev or c_prev < lb_prev else lb_prev

        d_prev = float(direction.iloc[i - 1])
        if d_prev == -1 and c_curr > upper_band.iloc[i]:
            direction.iloc[i] = 1
        elif d_prev == 1 and c_curr < lower_band.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = d_prev

        st.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]

    return pd.DataFrame({"direction": direction, "line": st})


def vwap_std_bands(df: pd.DataFrame, num_std: float = 2.0) -> pd.DataFrame:
    """VWAP with upper/lower standard-deviation bands (session-anchored)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    v = (tp * df["volume"]).cumsum() / cum_vol.replace(0, np.nan)

    variance = (((tp - v) ** 2 * df["volume"]).cumsum() / cum_vol.replace(0, np.nan)).clip(lower=0)
    std = variance ** 0.5
    return pd.DataFrame({"vwap": v, "upper": v + num_std * std, "lower": v - num_std * std})


def bars_to_frame(bars) -> pd.DataFrame:
    """Convert list[Bar] to a DataFrame indexed by timestamp."""
    rows = [
        {
            "timestamp": b.timestamp,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
            "vwap": b.vwap,
        }
        for b in bars
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("timestamp").sort_index()
    return df
