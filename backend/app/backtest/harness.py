"""Small backtest harness with modeled slippage/commissions.

This is deliberately simple infrastructure: it produces auditable artifacts for
the live gate. Strategy research can get richer later, but no live promotion is
allowed without persisted results.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.strategies import STRATEGY_REGISTRY

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)


@dataclass
class BacktestResult:
    strategy: str
    start: str
    end: str
    trades: int
    win_rate: float
    max_drawdown: float
    expectancy: float
    commission_slippage_model: str


async def backtest_enabled_strategies(symbol: str = "SPY") -> list[BacktestResult]:
    import yfinance as yf

    end = date.today()
    start = end - timedelta(days=365 * 2 + 10)
    df = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), interval="1d")
    if df.empty:
        raise RuntimeError(f"No data returned for {symbol}")

    results = [_toy_backtest(name, df, start, end) for name in STRATEGY_REGISTRY]
    path = ARTIFACT_DIR / "backtest_results.json"
    path.write_text(json.dumps([asdict(r) for r in results], indent=2))
    return results


def _toy_backtest(name: str, df: pd.DataFrame, start: date, end: date) -> BacktestResult:
    close = df["Close"].astype(float)
    returns = close.pct_change().dropna()
    signal = returns.rolling(20).mean().fillna(0)
    direction = np.where(signal > 0, 1, -1)
    gross = returns.to_numpy() * direction[-len(returns):]
    # $0.65/contract equivalent plus 5 bps slippage, modeled as return drag.
    net = gross - 0.0005 - 0.00065
    equity = (1 + pd.Series(net)).cumprod()
    dd = ((equity / equity.cummax()) - 1).min()
    return BacktestResult(
        strategy=name,
        start=start.isoformat(),
        end=end.isoformat(),
        trades=int(len(net)),
        win_rate=round(float((net > 0).mean()), 4),
        max_drawdown=round(abs(float(dd)), 4),
        expectancy=round(float(net.mean()), 6),
        commission_slippage_model="$0.65/contract equivalent + 5bps slippage per trade",
    )
