from __future__ import annotations

import numpy as np
import pandas as pd

from app.models.domain import AssetClass, Direction, Signal, SignalStatus
from app.strategies.base import Strategy, StrategyContext, registry
from app.utils.indicators import atr

PAIRS: list[tuple[str, str]] = [
    ("NVDA", "AMD"),
    ("MSFT", "AAPL"),
    ("JPM",  "GS"),
    ("COIN", "MSTR"),
]


@registry.register
class StatisticalArbitrageStrategy(Strategy):
    """Log-price spread mean-reversion (pairs trading).

    For each pair (A, B) the strategy computes the spread:
        s_t = log(P_A,t) − log(P_B,t)

    A rolling 60-bar mean μ and standard deviation σ produce the z-score:
        z_t = (s_t − μ_t) / σ_t

    Entry: |z| > entry_z (spread has deviated significantly from equilibrium).
    Exit:  |z| < exit_z  (spread has reverted toward the mean).
    Stop:  |z| > stop_z  (divergence; spread is not mean-reverting).

    The strategy is passed via StrategyContext:
        ctx.symbol      → the CHEAP leg (the one to BUY)
        ctx.bars        → OHLCV DataFrame for ctx.symbol
        ctx.extra["peer_symbol"] → the EXPENSIVE leg (symbol B)
        ctx.extra["peer_bars"]   → OHLCV DataFrame for the peer

    When z < −entry_z: symbol A is cheap relative to B → BUY A (bullish signal).
    When z >  entry_z: symbol B is cheap relative to A → BUY B (bullish signal
        emitted with ctx.symbol swapped to B, handled in signal_service).

    Signal mechanics:
        entry     = last price of the cheap symbol
        take_profit = price implied when z reverts to exit_z
            TP_A ≈ entry × exp(μ + exit_z × σ − log(P_B))
        stop_loss = price implied when z reaches stop_z
            SL_A ≈ entry × exp(μ − stop_z × σ − log(P_B))

    Confidence formula:
        base = 0.50
        magnitude component (+0.25 max): how far beyond entry_z
        correlation component (+0.15 max): rolling 60-bar correlation of A and B
            (high correlation strengthens mean-reversion assumption)
    """

    name = "stat_arb_pairs"
    description = (
        "Log-price spread z-score mean reversion across correlated pairs "
        "(NVDA/AMD, MSFT/AAPL, JPM/GS, COIN/MSTR)."
    )
    timeframe = "5Min"
    lookback_bars = 80
    default_params = {
        "spread_window": 60,
        "entry_z": 2.0,
        "exit_z": 0.25,
        "stop_z": 3.5,
        "min_correlation": 0.40,
        "atr_period": 14,
        "min_confidence": 0.48,
    }

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        df_a = ctx.bars
        df_b: pd.DataFrame | None = ctx.extra.get("peer_bars")
        peer_sym: str | None = ctx.extra.get("peer_symbol")

        if df_a is None or df_b is None or peer_sym is None:
            return []
        if len(df_a) < self.lookback_bars or len(df_b) < self.lookback_bars:
            return []

        p = self.params

        close_a = df_a["close"]
        close_b = df_b["close"]

        # Align on shared timestamps
        aligned = pd.concat([
            np.log(close_a).rename("log_a"),
            np.log(close_b).rename("log_b"),
        ], axis=1).dropna()
        if len(aligned) < p["spread_window"] + 10:
            return []

        spread = aligned["log_a"] - aligned["log_b"]
        mu = spread.rolling(p["spread_window"]).mean()
        sigma = spread.rolling(p["spread_window"]).std()
        z = (spread - mu) / sigma.replace(0, np.nan)

        last_z = float(z.iloc[-1])
        last_mu = float(mu.iloc[-1])
        last_sigma = float(sigma.iloc[-1])

        if any(np.isnan(v) for v in (last_z, last_mu, last_sigma)):
            return []
        if last_sigma <= 0:
            return []

        correlation = float(aligned["log_a"].rolling(p["spread_window"]).corr(
            aligned["log_b"]
        ).iloc[-1])
        if np.isnan(correlation) or correlation < p["min_correlation"]:
            return []

        long_a = last_z < -p["entry_z"]
        long_b = last_z > p["entry_z"]
        if not (long_a or long_b):
            return []

        # Symbol we BUY is always the cheap leg of the spread
        if long_a:
            buy_sym = ctx.symbol
            buy_close = float(close_a.iloc[-1])
            buy_df = df_a
        else:
            buy_sym = peer_sym
            buy_close = float(close_b.iloc[-1])
            buy_df = df_b

        sell_sym = peer_sym if long_a else ctx.symbol

        last_atr = float(atr(buy_df["high"], buy_df["low"], buy_df["close"], p["atr_period"]).iloc[-1])
        if np.isnan(last_atr) or last_atr <= 0:
            return []

        # Project take-profit and stop-loss from z-score targets back to price
        # TP: spread reverts to exit_z toward zero
        exit_log_spread = last_mu + p["exit_z"] * last_sigma * np.sign(last_z) * -1
        stop_log_spread = last_mu - p["stop_z"] * last_sigma * np.sign(last_z) * -1

        if long_a:
            peer_price = float(close_b.iloc[-1])
            tp_price = round(float(np.exp(exit_log_spread + np.log(peer_price))), 2)
            sl_price = round(float(np.exp(stop_log_spread + np.log(peer_price))), 2)
        else:
            peer_price = float(close_a.iloc[-1])
            tp_price = round(float(np.exp(-exit_log_spread + np.log(peer_price))), 2)
            sl_price = round(float(np.exp(-stop_log_spread + np.log(peer_price))), 2)

        # Fallback to ATR-based levels if projection is degenerate
        if tp_price <= buy_close or np.isnan(tp_price):
            tp_price = round(buy_close + 2.5 * last_atr, 2)
        if sl_price >= buy_close or np.isnan(sl_price):
            sl_price = round(buy_close - 1.5 * last_atr, 2)
        if sl_price >= buy_close:
            return []

        risk = buy_close - sl_price
        rr = round((tp_price - buy_close) / max(risk, 0.01), 2)
        if rr < 1.5:
            return []

        abs_z = abs(last_z)
        magnitude_score = min(1.0, (abs_z - p["entry_z"]) / (p["stop_z"] - p["entry_z"]))
        corr_score = min(1.0, max(0.0, (correlation - p["min_correlation"]) / (1.0 - p["min_correlation"])))
        confidence = round(
            min(1.0, max(0.0, 0.50 + 0.25 * magnitude_score + 0.15 * corr_score)),
            3,
        )
        if confidence < p["min_confidence"]:
            return []

        return [Signal(
            strategy=self.name,
            asset_class=AssetClass.STOCK,
            symbol=buy_sym,
            direction=Direction.BULLISH,
            entry=round(buy_close, 2),
            stop_loss=sl_price,
            take_profit=tp_price,
            confidence=confidence,
            reason=(
                f"Stat arb: {ctx.symbol}/{peer_sym} spread z-score {last_z:.2f} "
                f"({'cheap ' + ctx.symbol if long_a else 'cheap ' + peer_sym}). "
                f"Pair correlation {correlation:.2f}. σ={last_sigma:.4f}. "
                f"Revert target z={p['exit_z']} → TP ${tp_price:.2f}."
            ),
            invalidation=(
                f"Z-score reaches {p['stop_z']:.1f} (divergence, not reversion). "
                f"Close below ${sl_price:.2f}."
            ),
            risk_reward=rr,
            suggested_qty=1,
            suitable_for_options=False,
            holding_period_hint="intraday-to-swing",
            generated_at=ctx.now,
            status=SignalStatus.NEW,
            metadata={
                "pair": f"{ctx.symbol}/{peer_sym}",
                "z_score": round(last_z, 3),
                "spread_mu": round(last_mu, 5),
                "spread_sigma": round(last_sigma, 5),
                "correlation": round(correlation, 3),
                "cheap_leg": buy_sym,
                "expensive_leg": sell_sym,
                "regime": ctx.market_regime,
            },
        )]

    def validate(self) -> list[str]:
        issues = []
        p = self.params
        if not (1.0 <= p["entry_z"] < p["stop_z"]):
            issues.append("entry_z must be in [1.0, stop_z)")
        if not (0 <= p["exit_z"] < p["entry_z"]):
            issues.append("exit_z must be in [0, entry_z)")
        return issues
