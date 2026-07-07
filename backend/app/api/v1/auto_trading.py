"""Auto-trading control endpoints.

GET  /auto-trading/status   — current state + recent activity
POST /auto-trading/enable   — turn on autonomous paper trading
POST /auto-trading/disable  — turn off (manual-only mode)
POST /auto-trading/run-now  — trigger one cycle immediately (on demand)
GET  /auto-trading/activity — recent auto-trade events
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.brokers.base import BrokerOrder
from app.brokers.factory import get_broker
from app.core.logging import get_logger
from app.database.models import AuditLog, RiskSettings, SystemEvent
from app.database.session import session_factory
from app.market.session import classify_us_equity_session
from app.services.auto_trader import is_auto_trading_enabled

log = get_logger(__name__)
router = APIRouter(prefix="/auto-trading", tags=["auto-trading"])


async def _get_or_create_risk() -> RiskSettings:
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return row


@router.get("/status")
async def status() -> dict:
    """Current auto-trading state and recent activity summary."""
    async with session_factory()() as s:
        rs_res = await s.execute(select(RiskSettings).limit(1))
        rs = rs_res.scalar_one_or_none()

        # Last 5 auto-trade events
        ev_res = await s.execute(
            select(SystemEvent)
            .where(SystemEvent.kind == "auto_trade")
            .order_by(SystemEvent.created_at.desc())
            .limit(5)
        )
        events = ev_res.scalars().all()

    session_info = classify_us_equity_session()
    orders_by_id = await _current_orders_by_id(events)
    return {
        "auto_trading_enabled": bool(rs and rs.auto_trading_enabled),
        "kill_switch_active": bool(rs and rs.kill_switch_active),
        "effectively_active": bool(rs and rs.auto_trading_enabled and not rs.kill_switch_active),
        "market_session": session_info.session.value,
        "trading_phase": session_info.phase.value,
        "recent_trades": [_event_payload(e, orders_by_id) for e in events],
    }


@router.post("/enable")
async def enable() -> dict:
    """Enable autonomous paper trading."""
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.flush()
        row.auto_trading_enabled = True
        s.add(AuditLog(
            actor="user",
            action="auto_trading.enabled",
            target=row.id,
            detail={"enabled": True},
            severity="warn",
        ))
        await s.commit()
    log.warning("auto_trading.enabled")
    return {"auto_trading_enabled": True, "message": "Autonomous paper trading is now ON."}


@router.post("/disable")
async def disable() -> dict:
    """Disable autonomous paper trading (revert to manual-only)."""
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.flush()
        row.auto_trading_enabled = False
        s.add(AuditLog(
            actor="user",
            action="auto_trading.disabled",
            target=row.id,
            detail={"enabled": False},
            severity="info",
        ))
        await s.commit()
    log.info("auto_trading.disabled")
    return {"auto_trading_enabled": False, "message": "Autonomous trading disabled. Manual-only mode."}


@router.post("/run-now")
async def run_now() -> dict:
    """Trigger one AutoTradeLoop scan+trade cycle immediately."""
    enabled = await is_auto_trading_enabled()
    if not enabled:
        return {
            "skipped": True,
            "reason": "auto_trading_disabled — enable it first via POST /auto-trading/enable",
        }

    log.info("auto_trading.run_now")
    from app.auto_trade.loop import AutoTradeLoop

    result = await AutoTradeLoop().scan_and_trade()
    return {
        **result,
        "signals": int(result.get("proposals", 0) or 0),
        "executed": int(result.get("submitted", 0) or 0),
        "session": classify_us_equity_session().session.value,
        "results": [],
    }


@router.get("/activity")
async def activity(limit: int = 50) -> list[dict]:
    """Recent auto-trading events from the system log."""
    async with session_factory()() as s:
        res = await s.execute(
            select(SystemEvent)
            .where(SystemEvent.kind.in_(["auto_trade", "auto_exit"]))
            .order_by(SystemEvent.created_at.desc())
            .limit(limit)
        )
        events = res.scalars().all()
    orders_by_id = await _current_orders_by_id(events)
    return [_event_payload(e, orders_by_id, include_id=True) for e in events]


async def _current_orders_by_id(events: list[SystemEvent]) -> dict[str, BrokerOrder]:
    order_ids = {
        order_id
        for event in events
        if (order_id := _payload_order_id(event.payload or {}))
    }
    if not order_ids:
        return {}
    try:
        broker = await get_broker()
        orders = await broker.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_trading.order_status_refresh_failed", error=str(exc))
        return {}
    return {order.id: order for order in orders if order.id in order_ids}


def _event_payload(
    event: SystemEvent,
    orders_by_id: dict[str, BrokerOrder],
    *,
    include_id: bool = False,
) -> dict:
    payload = dict(event.payload or {})
    _refresh_order_payload(payload, orders_by_id)
    out = {
        "message": event.message,
        "payload": payload,
        "time": event.created_at.isoformat() if event.created_at else None,
    }
    if include_id:
        out.update({"id": event.id, "kind": event.kind, "severity": event.severity})
    return out


def _payload_order_id(payload: dict) -> str | None:
    fill = payload.get("fill")
    if isinstance(fill, dict) and fill.get("order_id"):
        return str(fill["order_id"])
    if payload.get("order_id"):
        return str(payload["order_id"])
    return None


def _refresh_order_payload(
    payload: dict,
    orders_by_id: dict[str, BrokerOrder],
) -> None:
    order_id = _payload_order_id(payload)
    if not order_id:
        return
    order = orders_by_id.get(order_id)
    if order is None:
        return

    raw = order.raw or {}
    order_snapshot = {
        "order_id": order.id,
        "status": order.status.value,
        "filled_qty": order.filled_qty,
        "avg_fill_price": order.avg_fill_price,
        "submitted_at": raw.get("submitted_at"),
        "filled_at": raw.get("filled_at"),
        "canceled_at": raw.get("canceled_at"),
        "failed_at": raw.get("failed_at"),
        "symbol": raw.get("symbol"),
        "side": raw.get("side"),
        "qty": raw.get("qty"),
        "order_type": raw.get("order_type", raw.get("type")),
        "limit_price": raw.get("limit_price"),
    }
    fill = payload.get("fill")
    if isinstance(fill, dict):
        fill.update(order_snapshot)
        payload["fill"] = fill
    else:
        payload["order"] = order_snapshot
