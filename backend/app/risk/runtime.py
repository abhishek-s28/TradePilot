"""Runtime risk context shared by manual and automated execution paths."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.brokers.base import BrokerAdapter, BrokerOrder
from app.core.logging import get_logger
from app.database.models import OrderRow, PositionRow
from app.database.session import session_factory
from app.market.session import MarketSessionInfo, classify_us_equity_session
from app.models.domain import AccountSnapshot as DomainAccountSnapshot
from app.models.domain import OrderStatus
from app.risk.manager import AccountSnapshot as RiskAccountSnapshot
from app.risk.manager import RiskState

log = get_logger(__name__)


@dataclass
class RuntimeRiskContext:
    broker_account: DomainAccountSnapshot
    risk_account: RiskAccountSnapshot
    positions: list[Any]
    state: RiskState
    session_info: MarketSessionInfo


async def load_runtime_risk_context(
    broker: BrokerAdapter,
    *,
    session_info: MarketSessionInfo | None = None,
) -> RuntimeRiskContext:
    """Build the live state RiskManager needs immediately before execution."""
    session_info = session_info or classify_us_equity_session()
    account = await broker.get_account()
    positions = await broker.get_positions()
    broker_orders = await _safe_broker_orders(broker)
    account_names = _ledger_account_names(broker)

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    active_statuses = [
        OrderStatus.PENDING.value,
        OrderStatus.SUBMITTED.value,
        OrderStatus.PARTIALLY_FILLED.value,
    ]
    counted_statuses = [*active_statuses, OrderStatus.FILLED.value]

    async with session_factory()() as session:
        count_res = await session.execute(
            select(func.count(OrderRow.id)).where(
                OrderRow.account.in_(account_names),
                OrderRow.status.in_(counted_statuses),
                OrderRow.submitted_at >= today_start,
            )
        )
        trades_today = int(count_res.scalar() or 0)

        pending_res = await session.execute(
            select(OrderRow).where(
                OrderRow.account.in_(account_names),
                OrderRow.status.in_(active_statuses),
                OrderRow.submitted_at >= today_start,
            )
        )
        pending_orders = pending_res.scalars().all()
        pending_symbols = set()
        for order in pending_orders:
            pending_symbols.add(order.symbol)
            root = _underlying_from_order_row(order)
            if root:
                pending_symbols.add(root)

        recent_res = await session.execute(
            select(PositionRow)
            .where(
                PositionRow.account.in_(account_names),
                PositionRow.closed_at.is_not(None),
            )
            .order_by(PositionRow.closed_at.desc())
            .limit(10)
        )
        recent_closed = recent_res.scalars().all()

    for order in broker_orders:
        if order.status in {
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            symbol = _order_symbol(order)
            if symbol:
                pending_symbols.add(symbol)
                pending_symbols.add(_root_symbol(symbol))

    consecutive_losses = 0
    last_loss_at: datetime | None = None
    for position in recent_closed:
        if position.realized_pnl < 0:
            consecutive_losses += 1
            if last_loss_at is None:
                last_loss_at = _aware(position.closed_at)
        else:
            break

    risk_account = RiskAccountSnapshot(
        cash=account.cash,
        equity=account.equity,
        buying_power=account.buying_power,
        realized_pnl_today=account.daily_pnl,
        unrealized_pnl_today=0.0,
    )
    state = RiskState(
        daily_realized_pnl=account.daily_pnl,
        daily_unrealized_pnl=0.0,
        open_positions=positions,
        trades_today=trades_today,
        consecutive_losses=consecutive_losses,
        last_loss_at=last_loss_at,
        pending_symbols=pending_symbols,
        market_session=session_info.session.value,
    )
    return RuntimeRiskContext(
        broker_account=account,
        risk_account=risk_account,
        positions=positions,
        state=state,
        session_info=session_info,
    )


async def _safe_broker_orders(broker: BrokerAdapter) -> list[BrokerOrder]:
    try:
        return await broker.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("risk_context.broker_orders_unavailable", broker=broker.name, error=str(exc))
        return []


def _ledger_account_names(broker: BrokerAdapter) -> list[str]:
    names = [broker.name]
    if broker.name == "paper":
        names.append("paper")
    return sorted(set(names))


def _order_symbol(order: BrokerOrder) -> str | None:
    raw = order.raw or {}
    for key in ("symbol", "asset_symbol"):
        value = raw.get(key)
        if value:
            return str(value)
    nested = raw.get("order") if isinstance(raw.get("order"), dict) else None
    if nested and nested.get("symbol"):
        return str(nested["symbol"])
    return None


def _underlying_from_order_row(order: OrderRow) -> str | None:
    payload = order.payload or {}
    signal_values = payload.get("signal_values") or {}
    for key in ("underlying_symbol", "underlying"):
        value = signal_values.get(key) or payload.get(key)
        if value:
            return str(value)
    return _root_symbol(order.symbol)


def _root_symbol(symbol: str) -> str:
    for i in range(1, len(symbol) - 14):
        chunk = symbol[i:i + 6]
        right = symbol[i + 6:i + 7]
        strike = symbol[i + 7:i + 15]
        if right in {"C", "P"} and chunk.isdigit() and strike.isdigit():
            return symbol[:i]
    return symbol


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
