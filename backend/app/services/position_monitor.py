"""Position exit monitor.

Runs independently of entry scans.  It checks stop-loss/take-profit levels for
local paper positions and broker-backed positions that were opened by the
auto-trader, then submits sell orders when exits trigger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.brokers.factory import get_broker
from app.core.logging import get_logger
from app.data.factory import get_provider
from app.database.models import OrderRow, PositionRow, SystemEvent
from app.database.session import session_factory
from app.market.session import MarketSessionInfo, classify_us_equity_session
from app.models.domain import (
    AssetClass,
    OrderProposal,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)
from app.services.order_journal import record_broker_order

log = get_logger(__name__)


@dataclass(frozen=True)
class ExitControls:
    stop_loss: float | None
    take_profit: float | None
    source: str


async def run_position_monitor() -> dict:
    broker = await get_broker()
    provider = await get_provider()
    session_info = classify_us_equity_session()
    positions = await broker.get_positions()

    checked = 0
    exited = 0
    skipped: list[dict] = []

    for pos in positions:
        checked += 1
        try:
            controls = await _load_exit_controls(pos.symbol, broker.name)
            if controls is None or (
                controls.stop_loss is None and controls.take_profit is None
            ):
                skipped.append({"symbol": pos.symbol, "reason": "no_exit_controls"})
                continue
            if await _has_pending_exit(pos.symbol, broker.name):
                skipped.append({"symbol": pos.symbol, "reason": "pending_exit_order"})
                continue

            price = await _position_price(provider, pos)
            hit_stop = controls.stop_loss is not None and price <= controls.stop_loss
            hit_target = controls.take_profit is not None and price >= controls.take_profit
            if not (hit_stop or hit_target):
                continue

            if pos.asset_class == AssetClass.OPTION and not session_info.can_open_option:
                skipped.append({
                    "symbol": pos.symbol,
                    "reason": "options_regular_hours_only",
                })
                continue
            if pos.asset_class == AssetClass.STOCK and not session_info.can_open_stock:
                skipped.append({"symbol": pos.symbol, "reason": "stock_session_closed"})
                continue

            reason = "stop_loss" if hit_stop else "take_profit"
            proposal = _exit_proposal(pos, price, reason, session_info)
            order = await broker.place_order(proposal)
            await record_broker_order(account=broker.name, proposal=proposal, order=order)
            await _log_exit_event(pos, price, reason, controls, order.id, order.status.value)
            exited += 1
            log.info(
                "position.auto_exit",
                symbol=pos.symbol,
                reason=reason,
                price=price,
                order_status=order.status.value,
                session=session_info.session.value,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("position_monitor.position_failed", symbol=pos.symbol, error=str(exc))
            skipped.append({"symbol": pos.symbol, "reason": f"error:{exc}"})

    return {
        "checked": checked,
        "exited": exited,
        "skipped": skipped,
        "session": session_info.session.value,
        "phase": session_info.phase.value,
    }


async def _position_price(provider, pos: Position) -> float:
    if pos.asset_class == AssetClass.STOCK:
        quote = await provider.get_quote(pos.symbol)
        return float(quote.mid)
    contract = await provider.get_option_quote(pos.symbol)
    return float(contract.mid or pos.current_price or pos.avg_price)


def _exit_proposal(
    pos: Position,
    price: float,
    reason: str,
    session_info: MarketSessionInfo,
) -> OrderProposal:
    extended = session_info.allows_extended_hours and pos.asset_class == AssetClass.STOCK
    # A sell limit just below the reference price keeps extended-hours exits
    # valid while giving the broker room to fill in thinner books.
    limit_price = round(max(0.01, price * 0.995), 2)
    mult = 100 if pos.asset_class == AssetClass.OPTION else 1
    return OrderProposal(
        symbol=pos.symbol,
        asset_class=pos.asset_class,
        side=Side.SELL,
        qty=pos.qty,
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        time_in_force=TimeInForce.DAY,
        extended_hours=extended,
        estimated_cost=round(price * pos.qty * mult, 2),
        estimated_max_loss=0.0,
        reason=reason,
        risk_score=0.0,
    )


async def _load_exit_controls(symbol: str, account: str) -> ExitControls | None:
    async with session_factory()() as s:
        if account == "paper":
            pos_res = await s.execute(
                select(PositionRow).where(
                    PositionRow.account == "paper",
                    PositionRow.symbol == symbol,
                    PositionRow.closed_at.is_(None),
                ).limit(1)
            )
            pos = pos_res.scalar_one_or_none()
            if pos:
                return ExitControls(pos.stop_loss, pos.take_profit, "position_row")

        order_res = await s.execute(
            select(OrderRow)
            .where(
                OrderRow.symbol == symbol,
                OrderRow.side == Side.BUY.value,
                OrderRow.account.in_([account, "paper"]),
            )
            .order_by(OrderRow.submitted_at.desc())
            .limit(1)
        )
        order = order_res.scalar_one_or_none()
        if order and order.payload:
            return ExitControls(
                _as_float(order.payload.get("stop_loss")),
                _as_float(order.payload.get("take_profit")),
                "order_row",
            )

        event_res = await s.execute(
            select(SystemEvent)
            .where(SystemEvent.kind == "auto_trade")
            .order_by(SystemEvent.created_at.desc())
            .limit(100)
        )
        for event in event_res.scalars().all():
            payload = event.payload or {}
            if payload.get("symbol") == symbol:
                return ExitControls(
                    _as_float(payload.get("stop_loss")),
                    _as_float(payload.get("take_profit")),
                    "system_event",
                )
    return None


async def _has_pending_exit(symbol: str, account: str) -> bool:
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    async with session_factory()() as s:
        res = await s.execute(
            select(OrderRow.id)
            .where(
                OrderRow.symbol == symbol,
                OrderRow.side == Side.SELL.value,
                OrderRow.account.in_([account, "paper"]),
                OrderRow.status.in_([
                    OrderStatus.PENDING.value,
                    OrderStatus.SUBMITTED.value,
                    OrderStatus.PARTIALLY_FILLED.value,
                ]),
                OrderRow.submitted_at >= recent,
            )
            .limit(1)
        )
        return res.scalar_one_or_none() is not None


async def _log_exit_event(
    pos: Position,
    price: float,
    reason: str,
    controls: ExitControls,
    order_id: str,
    order_status: str,
) -> None:
    async with session_factory()() as s:
        s.add(SystemEvent(
            kind="auto_exit",
            message=f"AUTO EXIT: {pos.symbol} {reason} @ {price:.2f} ({order_status})",
            payload={
                "symbol": pos.symbol,
                "asset_class": pos.asset_class.value,
                "qty": pos.qty,
                "price": price,
                "reason": reason,
                "stop_loss": controls.stop_loss,
                "take_profit": controls.take_profit,
                "controls_source": controls.source,
                "order_id": order_id,
                "order_status": order_status,
                "time": datetime.now(timezone.utc).isoformat(),
            },
            severity="info",
        ))
        await s.commit()


def _as_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
