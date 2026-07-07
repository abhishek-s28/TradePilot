"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14 00:00:00

Creates every table the app uses. Mirrors app/database/models.py so a fresh
database started via Alembic ends up identical to one started via
Base.metadata.create_all() in dev.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # ── watchlists ──
    op.create_table(
        "watchlists",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("tickers", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_watchlists"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_watchlists_user_id_users"),
    )

    # ── signals ──
    op.create_table(
        "signals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("strategy", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("underlying", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("invalidation", sa.Text(), nullable=True, server_default=""),
        sa.Column("risk_reward", sa.Float(), nullable=True),
        sa.Column("suggested_qty", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("suitable_for_options", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("holding_period_hint", sa.String(), nullable=True, server_default="intraday"),
        sa.Column("status", sa.String(), nullable=True, server_default="new"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
    )
    op.create_index("ix_signals_strategy", "signals", ["strategy"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])
    op.create_index("ix_signals_underlying", "signals", ["underlying"])
    op.create_index("ix_signals_status", "signals", ["status"])
    op.create_index("ix_signals_generated_at", "signals", ["generated_at"])

    # ── orders ──
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=True),
        sa.Column("account", sa.String(), nullable=True, server_default="paper"),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=True, server_default="pending"),
        sa.Column("filled_qty", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("broker_order_id", sa.String(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], name="fk_orders_signal_id_signals"),
    )
    op.create_index("ix_orders_account", "orders", ["account"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # ── positions ──
    op.create_table(
        "positions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=True, server_default="paper"),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=True, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_positions"),
        sa.UniqueConstraint("account", "symbol", "opened_at", name="uq_position_open"),
    )
    op.create_index("ix_positions_account", "positions", ["account"])
    op.create_index("ix_positions_symbol", "positions", ["symbol"])

    # ── paper_accounts ──
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("starting_cash", sa.Float(), nullable=True, server_default="100000"),
        sa.Column("cash", sa.Float(), nullable=True, server_default="100000"),
        sa.Column("realized_pnl", sa.Float(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_paper_accounts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_paper_accounts_user_id_users"),
    )

    # ── risk_settings ──
    op.create_table(
        "risk_settings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("max_daily_loss_usd", sa.Float(), nullable=True, server_default="100"),
        sa.Column("max_trade_loss_usd", sa.Float(), nullable=True, server_default="35"),
        sa.Column("max_open_positions", sa.Integer(), nullable=True, server_default="3"),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=True, server_default="6"),
        sa.Column("max_option_premium_usd", sa.Float(), nullable=True, server_default="75"),
        sa.Column("cooldown_after_losses", sa.Integer(), nullable=True, server_default="2"),
        sa.Column("allowed_strategies", sa.JSON(), nullable=True),
        sa.Column("allowed_tickers", sa.JSON(), nullable=True),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("auto_trading_enabled", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_risk_settings"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_risk_settings_user_id_users"),
    )

    # ── strategy_configs ──
    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_configs"),
        sa.UniqueConstraint("name", name="uq_strategy_configs_name"),
    )

    # ── audit_logs ──
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True, server_default="info"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    # ── system_events ──
    op.create_table(
        "system_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True, server_default="info"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_system_events"),
    )
    op.create_index("ix_system_events_kind", "system_events", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_system_events_kind", table_name="system_events")
    op.drop_table("system_events")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("strategy_configs")
    op.drop_table("risk_settings")
    op.drop_table("paper_accounts")
    op.drop_index("ix_positions_symbol", table_name="positions")
    op.drop_index("ix_positions_account", table_name="positions")
    op.drop_table("positions")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_symbol", table_name="orders")
    op.drop_index("ix_orders_account", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_signals_generated_at", table_name="signals")
    op.drop_index("ix_signals_status", table_name="signals")
    op.drop_index("ix_signals_underlying", table_name="signals")
    op.drop_index("ix_signals_symbol", table_name="signals")
    op.drop_index("ix_signals_strategy", table_name="signals")
    op.drop_table("signals")
    op.drop_table("watchlists")
    op.drop_table("users")
