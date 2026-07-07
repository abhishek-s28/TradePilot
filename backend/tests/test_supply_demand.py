"""Tests for the supply/demand volume-profile and zone-detection module."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.utils.supply_demand import (
    confluence_adjustment,
    find_zones,
    volume_profile,
    zone_context,
)


def _frame(opens, highs, lows, closes, volumes) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def test_volume_profile_poc_matches_high_volume_bin():
    # 5 bars, each spanning exactly one price unit: [100,101) .. [104,105).
    closes = [100.5, 101.5, 102.5, 103.5, 104.5]
    lows = [100, 101, 102, 103, 104]
    highs = [101, 102, 103, 104, 105]
    volumes = [10, 10, 1000, 10, 10]
    df = _frame(closes, highs, lows, closes, volumes)

    result = volume_profile(df, bins=5, value_area_pct=0.70)

    # Bin 2 (102-103) carries the bulk of the volume -> POC is its midpoint.
    assert result["poc"] == 102.5
    assert result["val"] <= result["poc"] <= result["vah"]
    assert result["val"] >= 100
    assert result["vah"] <= 105


def test_volume_profile_empty_df_returns_none():
    df = _frame([], [], [], [], [])
    result = volume_profile(df)
    assert result == {"poc": None, "vah": None, "val": None, "profile": []}


def test_find_zones_detects_demand_zone_on_volume_spike_and_rally():
    # Flat-ish prices, then a high-volume swing low at index 7 followed by a rally.
    n = 15
    closes = [100.0] * 7 + [95.0] + [96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0]
    lows = [c - 0.5 for c in closes]
    highs = [c + 0.5 for c in closes]
    opens = closes
    volumes = [1000] * 7 + [5000] + [1000] * 7

    df = _frame(opens, highs, lows, closes, volumes)
    zones = find_zones(df, swing_window=3, lookback=15, vol_mult=1.3)

    assert len(zones["demand"]) >= 1
    zone = zones["demand"][0]
    assert zone["index"] == 7
    assert zone["low"] == lows[7]
    assert zone["strength"] > 0


def test_find_zones_detects_supply_zone_on_volume_spike_and_selloff():
    # Flat-ish prices, then a high-volume swing high at index 7 followed by a selloff.
    n = 15
    closes = [100.0] * 7 + [105.0] + [104.0, 103.0, 102.0, 101.0, 100.0, 99.0, 98.0]
    lows = [c - 0.5 for c in closes]
    highs = [c + 0.5 for c in closes]
    opens = closes
    volumes = [1000] * 7 + [5000] + [1000] * 7

    df = _frame(opens, highs, lows, closes, volumes)
    zones = find_zones(df, swing_window=3, lookback=15, vol_mult=1.3)

    assert len(zones["supply"]) >= 1
    zone = zones["supply"][0]
    assert zone["index"] == 7
    assert zone["high"] == highs[7]
    assert zone["strength"] > 0


def test_find_zones_too_short_returns_empty():
    df = _frame([100, 101], [101, 102], [99, 100], [100, 101], [10, 10])
    assert find_zones(df) == {"demand": [], "supply": []}


def test_zone_context_location_above_value():
    # Most volume sits low in the range; final close is well above that area.
    closes = [100.5] * 8 + [110.5]
    lows = [100] * 8 + [110]
    highs = [101] * 8 + [111]
    opens = closes
    volumes = [1000] * 8 + [10]

    df = _frame(opens, highs, lows, closes, volumes)
    ctx = zone_context(df, profile_lookback=9)

    assert ctx["location"] == "above_value"
    assert ctx["last_close"] == 110.5


def test_confluence_adjustment_bullish_near_demand_is_positive():
    ctx = {
        "location": "in_value",
        "near_demand": True,
        "near_supply": False,
    }
    adj = confluence_adjustment(ctx, "bullish")
    assert adj > 0


def test_confluence_adjustment_bearish_near_supply_is_positive():
    ctx = {
        "location": "in_value",
        "near_demand": False,
        "near_supply": True,
    }
    adj = confluence_adjustment(ctx, "bearish")
    assert adj > 0


def test_confluence_adjustment_bullish_into_supply_is_negative():
    ctx = {
        "location": "above_value",
        "near_demand": False,
        "near_supply": True,
    }
    adj = confluence_adjustment(ctx, "bullish")
    assert adj < 0


def test_confluence_adjustment_empty_context_is_zero():
    assert confluence_adjustment({}, "bullish") == 0.0


def test_confluence_adjustment_clamped_to_bounds():
    ctx = {"location": "above_value", "near_demand": True, "near_supply": False}
    adj = confluence_adjustment(ctx, "bullish")
    assert -0.08 <= adj <= 0.08
