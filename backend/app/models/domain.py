"""Pure domain types. The vocabulary every module shares."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class AssetClass(str, Enum):
    STOCK = "stock"
    OPTION = "option"


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStatus(str, Enum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    PAPER_EXECUTED = "paper_executed"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


# ───────────────── Value Objects ─────────────────


class Quote(BaseModel):
    """A snapshot of NBBO + last trade."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: float
    ask: float
    last: float
    bid_size: int = 0
    ask_size: int = 0
    timestamp: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else self.last

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_pct(self) -> float:
        return (self.spread / self.mid) if self.mid > 0 else 1.0

    def is_stale(self, max_age_seconds: float, now: datetime) -> bool:
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() > max_age_seconds


class Bar(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None


class NewsItem(BaseModel):
    """A single news headline from the data provider's news feed."""
    model_config = ConfigDict(frozen=True)
    id: str
    headline: str
    summary: str = ""
    source: str = ""
    url: str = ""
    symbols: list[str] = Field(default_factory=list)
    created_at: datetime


class OptionContract(BaseModel):
    """A specific option contract — symbol + chain metadata."""
    model_config = ConfigDict(frozen=True)
    symbol: str           # OCC symbol, e.g. AAPL240119C00190000
    underlying: str
    expiration: datetime
    strike: float
    right: OptionRight
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else self.last

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 1.0
        return max(0.0, self.ask - self.bid) / self.mid

    @property
    def liquidity_score(self) -> float:
        """0..1. Combines OI, volume, and spread tightness."""
        oi_score = min(1.0, self.open_interest / 500.0)
        vol_score = min(1.0, self.volume / 100.0)
        spread_score = max(0.0, 1.0 - self.spread_pct * 5)  # 20% spread → 0
        return round(0.4 * oi_score + 0.3 * vol_score + 0.3 * spread_score, 3)


class Signal(BaseModel):
    """A trade idea produced by a strategy. Never executed directly —
    must pass through RiskManager → OrderProposal first."""
    id: Optional[str] = None
    strategy: str
    asset_class: AssetClass
    symbol: str                       # ticker for stock, OCC symbol for option
    underlying: Optional[str] = None  # for options
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    invalidation: str = ""
    risk_reward: Optional[float] = None
    suggested_qty: int = 1
    suitable_for_options: bool = False
    holding_period_hint: str = "intraday"
    generated_at: datetime
    status: SignalStatus = SignalStatus.NEW
    metadata: dict = Field(default_factory=dict)


class OrderProposal(BaseModel):
    """A risk-approved order ready for the broker (or paper engine)."""
    signal_id: Optional[str] = None
    strategy_name: str = ""
    symbol: str
    asset_class: AssetClass
    side: Side
    qty: int
    legs: list[str] = Field(default_factory=list)
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    extended_hours: bool = False
    estimated_cost: float
    estimated_max_loss: float
    max_risk_usd: float = 0.0
    est_cost_usd: float = 0.0
    signal_values: dict = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str
    risk_score: float = Field(ge=0.0, le=1.0)


class Position(BaseModel):
    symbol: str
    asset_class: AssetClass
    qty: int
    avg_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    opened_at: datetime

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price * (100 if self.asset_class == AssetClass.OPTION else 1)


class AccountSnapshot(BaseModel):
    cash: float
    equity: float
    buying_power: float
    positions_value: float
    daily_pnl: float = 0.0
    open_positions: int = 0
