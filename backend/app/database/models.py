"""SQLAlchemy ORM models. All tables defined here for visibility."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tickers: Mapped[list[str]] = mapped_column(JSON, default=list)


class SignalRow(Base, TimestampMixin):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    strategy: Mapped[str] = mapped_column(String, nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    underlying: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    risk_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    suggested_qty: Mapped[int] = mapped_column(Integer, default=1)
    suitable_for_options: Mapped[bool] = mapped_column(Boolean, default=False)
    holding_period_hint: Mapped[str] = mapped_column(String, default="intraday")
    status: Mapped[str] = mapped_column(String, default="new", index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_signals_generated_at", "generated_at"),)


class OrderRow(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    signal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("signals.id"), nullable=True)
    account: Mapped[str] = mapped_column(String, default="paper", index=True)  # paper | live
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False)
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PositionRow(Base, TimestampMixin):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account: Mapped[str] = mapped_column(String, default="paper", index=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("account", "symbol", "opened_at", name="uq_position_open"),
    )


class PaperAccount(Base, TimestampMixin):
    __tablename__ = "paper_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    starting_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class RiskSettings(Base, TimestampMixin):
    __tablename__ = "risk_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=1500.0)
    max_trade_loss_usd: Mapped[float] = mapped_column(Float, default=500.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=10)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=30)
    max_option_premium_usd: Mapped[float] = mapped_column(Float, default=500.0)
    cooldown_after_losses: Mapped[int] = mapped_column(Integer, default=2)
    allowed_strategies: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_tickers: Mapped[list[str]] = mapped_column(JSON, default=list)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class StrategyConfig(Base, TimestampMixin):
    __tablename__ = "strategy_configs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # user_id or "system"
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String, default="info")  # info | warn | error | critical


class SystemEvent(Base, TimestampMixin):
    __tablename__ = "system_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String, default="info")
