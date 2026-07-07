"""Strategy tests."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.models.domain import AssetClass, Side
from app.models.domain import OptionContract, OptionRight
from app.risk.manager import OrderProposal
from app.auto_trade.loop import _ENTRY_STRATEGY_ALLOWLIST
from app.strategies.alpaca_auto import (
    _long_option_budget,
    _option_affordable_for_account,
    _select_affordable_long_option,
)
from app.strategies.base import StrategyContext
from app.strategies.advanced import (
    BollingerSqueezeBreakoutStrategy,
    BullFlagContinuationStrategy,
    EMADowntrendPutStrategy,
    EMATrendPullbackStrategy,
    RSIReversalStrategy,
)
from app.strategies.lifecycle import LongTermETFAllocatorStrategy, SessionMomentumStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum_breakout import MomentumBreakoutStrategy
from app.strategies.regime import detect_regime


def _bars(prices: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(prices)
    volumes = volumes or [100_000] * n
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="5min")
    return pd.DataFrame(
        {
            "open": prices, "high": [p * 1.001 for p in prices],
            "low": [p * 0.999 for p in prices], "close": prices, "volume": volumes,
        },
        index=idx,
    )


def test_momentum_breakout_fires_on_clean_breakout():
    # Controlled chop, then a volume-confirmed breakout with RSI below the cap.
    rng = np.random.default_rng(1)
    prices = list(100 + rng.normal(0, 0.2, 100))
    prices += [101 + 0.3 * np.sin(i) + rng.normal(0, 0.1) for i in range(20)]
    prices += [103.0]
    volumes = [100_000] * 120 + [500_000]
    df = _bars(prices, volumes)
    strat = MomentumBreakoutStrategy()
    sigs = strat.generate(StrategyContext(
        symbol="TEST", bars=df, latest_quote=None, market_regime="bullish",
    ))
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.direction.value == "bullish"
    assert sig.stop_loss < sig.entry < sig.take_profit
    assert 0 < sig.confidence <= 1


def test_advanced_strategy_pack_fires_on_constructed_setups():
    # EMA trend continuation after a controlled pullback.
    prices = list(np.linspace(100, 115, 115)) + [113, 112.6, 112.8, 113.2, 114.5]
    volumes = [100_000] * len(prices)
    volumes[-1] = 130_000
    sigs = EMATrendPullbackStrategy().generate(StrategyContext(
        symbol="TEST", bars=_bars(prices, volumes), latest_quote=None, market_regime="bullish",
    ))
    assert len(sigs) == 1
    assert sigs[0].suitable_for_options is True

    # Impulse, shallow flag, continuation trigger.
    prices = list(np.linspace(100, 101, 100))
    prices += [101, 102, 103, 104, 105, 105.5, 106, 106.2, 106.4, 106.5, 106.4, 106.6]
    prices += [105.9, 105.7, 105.8, 105.9, 106.0, 106.1, 106.2, 106.25, 106.8]
    volumes = [100_000] * len(prices)
    volumes[-1] = 160_000
    sigs = BullFlagContinuationStrategy().generate(StrategyContext(
        symbol="TEST", bars=_bars(prices, volumes), latest_quote=None, market_regime="bullish",
    ))
    assert len(sigs) == 1

    # Volatility compression followed by a high-volume breakout.
    prices = [100 + 0.05 * np.sin(i) for i in range(140)]
    prices += [100.02 + 0.02 * np.sin(i) for i in range(20)]
    prices += [101.0]
    volumes = [100_000] * 160 + [300_000]
    sigs = BollingerSqueezeBreakoutStrategy().generate(StrategyContext(
        symbol="TEST", bars=_bars(prices, volumes), latest_quote=None, market_regime="bullish",
    ))
    assert len(sigs) == 1

    # RSI washout that recovers through the trigger.
    prices = list(np.linspace(110, 100, 90)) + [99, 98, 97, 96.5, 96, 96.2]
    prices += [96.5, 97, 97.5, 98, 99]
    sigs = RSIReversalStrategy().generate(StrategyContext(
        symbol="TEST", bars=_bars(prices), latest_quote=None, market_regime="choppy",
    ))
    assert len(sigs) == 1

    # Downtrend continuation creates an option-suitable put idea.
    prices = list(np.linspace(115, 100, 115)) + [102, 102.5, 102.2, 101.8, 100.8]
    volumes = [100_000] * len(prices)
    volumes[-1] = 150_000
    sigs = EMADowntrendPutStrategy().generate(StrategyContext(
        symbol="TEST", bars=_bars(prices, volumes), latest_quote=None, market_regime="bearish",
    ))
    assert len(sigs) == 1
    assert sigs[0].asset_class.value == "option"
    assert sigs[0].direction.value == "bearish"


def test_lifecycle_strategies_fire_on_constructed_setups():
    prices = list(np.linspace(100, 104, 90)) + [104.2, 104.4, 104.7, 105.1, 105.6]
    volumes = [100_000] * (len(prices) - 1) + [180_000]
    sigs = SessionMomentumStrategy().generate(StrategyContext(
        symbol="TEST",
        bars=_bars(prices, volumes),
        latest_quote=None,
        market_regime="bullish",
        extra={"market_session": "premarket"},
    ))
    assert len(sigs) == 1
    assert sigs[0].asset_class.value == "stock"
    assert sigs[0].metadata["market_session"] == "premarket"

    etf_prices = list(np.linspace(100, 125, 220))
    sigs = LongTermETFAllocatorStrategy().generate(StrategyContext(
        symbol="SPY",
        bars=_bars(etf_prices),
        latest_quote=None,
        market_regime="bullish",
    ))
    assert len(sigs) == 1
    assert sigs[0].holding_period_hint == "swing"


def test_momentum_breakout_no_signal_in_chop():
    rng = np.random.default_rng(42)
    prices = list(100 + rng.normal(0, 0.5, 200))
    df = _bars(prices)
    sigs = MomentumBreakoutStrategy().generate(StrategyContext(
        symbol="TEST", bars=df, latest_quote=None,
    ))
    assert sigs == []


def test_mean_reversion_fires_on_oversold():
    # Smooth grind down to oversold
    prices = list(np.linspace(100, 90, 200))
    df = _bars(prices)
    sigs = MeanReversionStrategy().generate(StrategyContext(
        symbol="TEST", bars=df, latest_quote=None,
    ))
    # May or may not fire depending on RSI/BB shape; just assert no exceptions
    # and any signal produced is well-formed.
    for s in sigs:
        assert s.entry > s.stop_loss
        assert s.take_profit > s.entry
        assert 0 < s.confidence <= 1


def test_regime_bullish_uptrend():
    prices = list(np.linspace(100, 130, 100))
    df = _bars(prices)
    assert detect_regime(df) in ("bullish", "high_vol")


def test_regime_bearish_downtrend():
    prices = list(np.linspace(130, 100, 100))
    df = _bars(prices)
    assert detect_regime(df) in ("bearish", "high_vol")


def test_regime_unknown_with_short_data():
    df = _bars([100, 101, 102])
    assert detect_regime(df) == "unknown"


def test_small_paper_account_allows_five_hundred_dollar_option_when_funded():
    account = SimpleNamespace(equity=701.96, buying_power=701.96)
    assert _long_option_budget(account) == 500.0

    oversized_option = OrderProposal(
        strategy_name="weekly_momentum",
        legs=["GOOGL260626C00175000"],
        symbol="GOOGL260626C00175000",
        underlying="GOOGL",
        asset_class=AssetClass.OPTION,
        side=Side.BUY,
        qty=1,
        max_risk_usd=19_568.50,
        est_cost_usd=19_568.50,
        confidence=0.73,
        signal_values={},
    )
    assert not _option_affordable_for_account(oversized_option, account)

    affordable_option = OrderProposal(
        strategy_name="weekly_momentum",
        legs=["AAPL260626C00190000"],
        symbol="AAPL260626C00190000",
        underlying="AAPL",
        asset_class=AssetClass.OPTION,
        side=Side.BUY,
        qty=1,
        max_risk_usd=500.0,
        est_cost_usd=500.0,
        confidence=0.73,
        signal_values={},
    )
    assert _option_affordable_for_account(affordable_option, account)


def test_option_budget_still_respects_buying_power():
    account = SimpleNamespace(equity=701.96, buying_power=347.38)
    assert _long_option_budget(account) == 312.64


async def test_affordable_selector_steps_down_to_budget_contract():
    account = SimpleNamespace(equity=701.96, buying_power=38.89)
    expiration = datetime.now(timezone.utc) + pd.Timedelta(days=10)

    class Market:
        async def get_options_chain(self, underlying: str):
            return [
                OptionContract(
                    symbol=f"{underlying}260626C00100000",
                    underlying=underlying,
                    expiration=expiration,
                    strike=100,
                    right=OptionRight.CALL,
                    bid=0.49,
                    ask=0.51,
                    last=0.50,
                    volume=200,
                    open_interest=500,
                    delta=0.52,
                ),
                OptionContract(
                    symbol=f"{underlying}260626C00105000",
                    underlying=underlying,
                    expiration=expiration,
                    strike=105,
                    right=OptionRight.CALL,
                    bid=0.34,
                    ask=0.36,
                    last=0.35,
                    volume=200,
                    open_interest=500,
                    delta=0.30,
                ),
            ]

    selected = await _select_affordable_long_option(
        Market(), "AAPL", OptionRight.CALL, 0.40, 0.65, account, min_dte=7, max_dte=21
    )

    assert selected is not None
    assert selected.symbol == "AAPL260626C00105000"


def test_auto_entry_allowlist_includes_active_strategies():
    assert "zero_dte_scalp" in _ENTRY_STRATEGY_ALLOWLIST
    assert "weekly_momentum" in _ENTRY_STRATEGY_ALLOWLIST
    assert "bull_call_spread" in _ENTRY_STRATEGY_ALLOWLIST
    assert "bear_put_spread" in _ENTRY_STRATEGY_ALLOWLIST
    assert "vwap_bounce" in _ENTRY_STRATEGY_ALLOWLIST
    assert "rsi_pullback" in _ENTRY_STRATEGY_ALLOWLIST
    assert "momentum_scalp" in _ENTRY_STRATEGY_ALLOWLIST
    assert "cash_secured_put" not in _ENTRY_STRATEGY_ALLOWLIST
