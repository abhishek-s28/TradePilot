"""Strategy framework.

Every strategy is a class with:
 - metadata (name, description, params schema)
 - `generate()` that takes market context and returns 0..N Signals
 - `validate()` self-check
 - explanation output

Strategies are pure with respect to data — they get bars/quotes passed in,
they never touch the network or DB directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.domain import Signal


@dataclass
class StrategyContext:
    """Everything a strategy needs to make a decision for one symbol."""
    symbol: str
    bars: Any                          # pandas DataFrame
    latest_quote: Any                  # Quote | None
    market_regime: str = "unknown"     # bullish | bearish | choppy | high_vol | unknown
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict = field(default_factory=dict)


class Strategy(ABC):
    name: str = "abstract"
    description: str = ""
    timeframe: str = "5Min"
    lookback_bars: int = 200
    default_params: dict[str, Any] = {}

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = {**self.default_params, **(params or {})}

    @abstractmethod
    def generate(self, ctx: StrategyContext) -> list[Signal]: ...

    def validate(self) -> list[str]:
        """Return list of validation issues, empty if OK."""
        return []

    def explain(self, signal: Signal) -> str:
        return signal.reason

    def risk_score(self, signal: Signal) -> float:
        """0=low risk, 1=high risk. Default: inverse of confidence."""
        return round(1.0 - signal.confidence, 3)


class StrategyRegistry:
    """Discovery point for all strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, type[Strategy]] = {}

    def register(self, cls: type[Strategy]) -> type[Strategy]:
        self._strategies[cls.name] = cls
        return cls

    def get(self, name: str) -> type[Strategy] | None:
        return self._strategies.get(name)

    def all(self) -> dict[str, type[Strategy]]:
        return dict(self._strategies)


registry = StrategyRegistry()
