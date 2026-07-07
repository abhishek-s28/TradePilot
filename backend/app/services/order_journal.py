"""Order journaling for broker adapters that do not write to the local DB."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.brokers.base import BrokerOrder
from app.database.models import OrderRow
from app.database.session import session_factory
from app.models.domain import OrderProposal, OrderStatus


async def record_broker_order(
    *,
    account: str,
    proposal: OrderProposal,
    order: BrokerOrder,
) -> None:
    """Persist or update a broker order row.

    The local paper engine records its own orders.  Alpaca/IBKR adapters return
    broker state but otherwise have no local order ledger, so the auto-trader
    needs this table for duplicate prevention and exit controls.
    """
    if account == "paper":
        return

    now = datetime.now(timezone.utc)
    async with session_factory()() as s:
        res = await s.execute(select(OrderRow).where(OrderRow.id == order.id))
        row = res.scalar_one_or_none()
        if row is None:
            row = OrderRow(
                id=order.id,
                signal_id=proposal.signal_id,
                account=account,
                symbol=proposal.symbol,
                asset_class=proposal.asset_class.value,
                side=proposal.side.value,
                qty=proposal.qty,
                order_type=proposal.order_type.value,
                limit_price=proposal.limit_price,
                stop_price=proposal.stop_price,
                submitted_at=now,
                broker_order_id=order.id,
            )
            s.add(row)

        row.status = order.status.value
        row.filled_qty = order.filled_qty
        row.avg_fill_price = order.avg_fill_price
        row.filled_at = now if order.status == OrderStatus.FILLED else row.filled_at
        row.rejection_reason = _rejection_reason(order)
        row.payload = {
            "strategy": proposal.strategy_name,
            "legs": proposal.legs,
            "reason": proposal.reason,
            "risk_score": proposal.risk_score,
            "computed_risk": {
                "max_risk_usd": proposal.max_risk_usd or proposal.estimated_max_loss,
                "est_cost_usd": proposal.est_cost_usd or proposal.estimated_cost,
            },
            "signal_values": proposal.signal_values,
            "stop_loss": proposal.stop_loss,
            "take_profit": proposal.take_profit,
            "extended_hours": proposal.extended_hours,
            "fill": {
                "filled_qty": order.filled_qty,
                "avg_fill_price": order.avg_fill_price,
                "status": order.status.value,
            },
            "raw": order.raw,
        }
        await s.commit()


def order_symbol(order: BrokerOrder) -> str | None:
    raw = order.raw or {}
    for key in ("symbol", "asset_symbol"):
        value = raw.get(key)
        if value:
            return str(value)
    nested = raw.get("order") if isinstance(raw.get("order"), dict) else None
    if nested and nested.get("symbol"):
        return str(nested["symbol"])
    return None


def _rejection_reason(order: BrokerOrder) -> str | None:
    raw = order.raw or {}
    for key in ("rejection_reason", "reject_reason", "failed_reason"):
        value = raw.get(key)
        if value:
            return str(value)
    return None
