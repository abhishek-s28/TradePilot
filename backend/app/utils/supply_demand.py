"""Supply & demand analysis: volume profile + swing-based zone detection.

These are the building blocks "real" discretionary traders use to decide
*where* a move is likely to start or stall — not just *that* momentum/RSI
looks favorable. Strategies use this module two ways:

1. As a standalone reversal strategy (`SupplyDemandReversal` in
   `alpaca_auto.py`) that buys bounces off demand zones / rejections off
   supply zones.
2. As a confidence adjustment (`confluence_adjustment`) layered onto existing
   momentum/trend/mean-reversion strategies — e.g. a bullish breakout that's
   also clearing the volume-profile POC with a demand zone below it is a much
   higher-quality setup than the same breakout deep into "premium" territory
   with a supply zone overhead.

All functions are pure (DataFrame in, dict out) for easy unit testing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_profile(df: pd.DataFrame, bins: int = 24, value_area_pct: float = 0.70) -> dict:
    """Compute a volume profile over the given bars.

    Returns the Point of Control (POC — price level with the most traded
    volume) and the Value Area High/Low (VAH/VAL — the price band containing
    `value_area_pct` of total volume, expanded outward from the POC).
    """
    if df.empty:
        return {"poc": None, "vah": None, "val": None, "profile": []}

    lo = float(df["low"].min())
    hi = float(df["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        last = float(df["close"].iloc[-1])
        return {"poc": last, "vah": last, "val": last, "profile": []}

    edges = np.linspace(lo, hi, bins + 1)
    vol_by_bin = np.zeros(bins)

    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)

    for bar_lo, bar_hi, vol in zip(lows, highs, vols):
        if vol <= 0:
            continue
        start_bin = int(np.searchsorted(edges, bar_lo, side="right") - 1)
        end_bin = int(np.searchsorted(edges, bar_hi, side="right") - 1)
        start_bin = min(max(start_bin, 0), bins - 1)
        end_bin = min(max(end_bin, 0), bins - 1)
        if start_bin > end_bin:
            start_bin, end_bin = end_bin, start_bin
        n = end_bin - start_bin + 1
        vol_by_bin[start_bin:end_bin + 1] += vol / n

    total = float(vol_by_bin.sum())
    if total <= 0:
        last = float(df["close"].iloc[-1])
        return {"poc": last, "vah": hi, "val": lo, "profile": []}

    poc_idx = int(np.argmax(vol_by_bin))
    poc_price = (edges[poc_idx] + edges[poc_idx + 1]) / 2

    target = total * value_area_pct
    lo_idx = hi_idx = poc_idx
    acc = vol_by_bin[poc_idx]
    while acc < target and (lo_idx > 0 or hi_idx < bins - 1):
        below = vol_by_bin[lo_idx - 1] if lo_idx > 0 else -1.0
        above = vol_by_bin[hi_idx + 1] if hi_idx < bins - 1 else -1.0
        if above >= below:
            hi_idx += 1
            acc += vol_by_bin[hi_idx]
        else:
            lo_idx -= 1
            acc += vol_by_bin[lo_idx]

    return {
        "poc": round(float(poc_price), 4),
        "vah": round(float(edges[hi_idx + 1]), 4),
        "val": round(float(edges[lo_idx]), 4),
        "profile": [
            (round(float((edges[i] + edges[i + 1]) / 2), 4), float(vol_by_bin[i]))
            for i in range(bins)
        ],
    }


def find_zones(df: pd.DataFrame, swing_window: int = 3, lookback: int = 60, vol_mult: float = 1.3) -> dict:
    """Detect recent supply (resistance) and demand (support) zones.

    A demand zone is the high/low range of a swing-low bar printed on
    relative volume >= `vol_mult` that price subsequently rallied away from
    (i.e. real buying showed up there). A supply zone is the mirror image at
    swing highs. Returns the most recent zones first, capped to 5 each.
    """
    if df.empty or len(df) < swing_window * 2 + 5:
        return {"demand": [], "supply": []}

    recent = df.iloc[-lookback:] if len(df) > lookback else df
    high = recent["high"].to_numpy(dtype=float)
    low = recent["low"].to_numpy(dtype=float)
    close = recent["close"].to_numpy(dtype=float)
    vol = recent["volume"].to_numpy(dtype=float)
    avg_vol = float(np.mean(vol)) if len(vol) else 0.0

    demand: list[dict] = []
    supply: list[dict] = []
    n = len(recent)

    for i in range(swing_window, n - swing_window):
        window_low = low[i - swing_window:i + swing_window + 1]
        window_high = high[i - swing_window:i + swing_window + 1]
        rel_vol = (vol[i] / avg_vol) if avg_vol > 0 else 0.0

        if low[i] == window_low.min() and rel_vol >= vol_mult:
            future = close[i + 1:i + 1 + swing_window]
            if len(future) and float(future.max()) > close[i]:
                move = (float(future.max()) - close[i]) / max(close[i], 1e-6)
                strength = round(min(1.0, rel_vol / 3.0) * min(1.0, move * 20), 4)
                demand.append({
                    "low": float(low[i]), "high": float(max(close[i], high[i])),
                    "index": i, "strength": strength,
                })

        if high[i] == window_high.max() and rel_vol >= vol_mult:
            future = close[i + 1:i + 1 + swing_window]
            if len(future) and float(future.min()) < close[i]:
                move = (close[i] - float(future.min())) / max(close[i], 1e-6)
                strength = round(min(1.0, rel_vol / 3.0) * min(1.0, move * 20), 4)
                supply.append({
                    "low": float(min(close[i], low[i])), "high": float(high[i]),
                    "index": i, "strength": strength,
                })

    demand.sort(key=lambda z: z["index"], reverse=True)
    supply.sort(key=lambda z: z["index"], reverse=True)
    return {"demand": demand[:5], "supply": supply[:5]}


def zone_context(df: pd.DataFrame, profile_lookback: int = 120) -> dict:
    """High-level supply/demand read for the current bar.

    Combines the volume profile (POC/value area) with swing-based zones into
    flags strategies can use as confluence filters:
      - location: "above_value" | "in_value" | "below_value" | "unknown"
      - near_demand / near_supply: price is currently testing a zone
      - nearest_demand / nearest_supply: the zone dicts themselves (or None)
    """
    if df.empty:
        return {}

    window = df.iloc[-profile_lookback:] if len(df) > profile_lookback else df
    vp = volume_profile(window)
    zones = find_zones(df)
    last_close = float(df["close"].iloc[-1])

    poc, vah, val = vp["poc"], vp["vah"], vp["val"]
    if vah is not None and val is not None and vah > val:
        if last_close > vah:
            location = "above_value"
        elif last_close < val:
            location = "below_value"
        else:
            location = "in_value"
    else:
        location = "unknown"

    def _nearest(zones_list: list[dict]) -> dict | None:
        if not zones_list:
            return None
        return min(zones_list, key=lambda z: abs((z["low"] + z["high"]) / 2 - last_close))

    nearest_demand = _nearest(zones["demand"])
    nearest_supply = _nearest(zones["supply"])

    # "Testing" a zone = price is inside it, or within 0.3% of its edge.
    near_demand = bool(
        nearest_demand
        and nearest_demand["low"] * 0.997 <= last_close <= nearest_demand["high"] * 1.01
    )
    near_supply = bool(
        nearest_supply
        and nearest_supply["low"] * 0.99 <= last_close <= nearest_supply["high"] * 1.003
    )

    return {
        "poc": poc, "vah": vah, "val": val,
        "location": location,
        "demand_zones": zones["demand"],
        "supply_zones": zones["supply"],
        "nearest_demand": nearest_demand,
        "nearest_supply": nearest_supply,
        "near_demand": near_demand,
        "near_supply": near_supply,
        "last_close": last_close,
    }


def confluence_adjustment(ctx: dict, direction: str) -> float:
    """Confidence delta in [-0.08, +0.08] from supply/demand confluence.

    `direction` is "bullish" (long calls / short puts) or "bearish" (long
    puts / short calls). Bouncing off a demand zone with room up to the next
    supply zone is a tailwind; chasing a move that's already deep into
    "premium" territory or running straight into a supply zone is a
    headwind — and vice versa for bearish setups.
    """
    if not ctx:
        return 0.0
    loc = ctx.get("location")
    adj = 0.0
    if direction == "bullish":
        if ctx.get("near_demand"):
            adj += 0.06
        if loc == "above_value":
            adj += 0.03
        elif loc == "below_value":
            adj -= 0.04
        if ctx.get("near_supply"):
            adj -= 0.05
    else:
        if ctx.get("near_supply"):
            adj += 0.06
        if loc == "below_value":
            adj += 0.03
        elif loc == "above_value":
            adj -= 0.04
        if ctx.get("near_demand"):
            adj -= 0.05
    return round(max(-0.08, min(0.08, adj)), 3)
