"""Order placement and management endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.brokers.factory import get_broker
from app.data.factory import get_provider
from app.database.models import OrderRow, SystemEvent
from app.database.session import session_factory
from app.models.domain import AssetClass, OrderProposal as DomainOrderProposal
from app.models.domain import OrderStatus, OrderType, Side, TimeInForce
from app.risk.loader import load_risk_config
from app.risk.manager import OrderProposal as RiskOrderProposal
from app.risk.manager import RiskManager
from app.risk.runtime import load_runtime_risk_context
from app.services.order_journal import record_broker_order

router = APIRouter(prefix="/orders", tags=["trading"])


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: Side
    qty: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    asset_class: AssetClass = AssetClass.STOCK
    extended_hours: bool = False


@router.get("")
async def list_orders() -> list[dict]:
    broker = await get_broker()
    orders = await broker.get_orders()
    return [_order_dict(o, broker.name) for o in orders]


@router.post("")
async def place_order(req: PlaceOrderRequest) -> dict:
    req = req.model_copy(update={"symbol": req.symbol.upper().strip()})
    _validate_manual_order(req)

    broker = await get_broker()
    provider = await get_provider()
    context = await load_runtime_risk_context(broker)
    closing = _is_closing_order(req, context.positions)
    if req.side == Side.SELL and not closing:
        raise HTTPException(
            400,
            "Manual short or naked sell orders are disabled. Sell only to close an existing position.",
        )

    ref_price = await _reference_price(req, provider)
    multiplier = 100 if req.asset_class == AssetClass.OPTION else 1
    est_cost = round(ref_price * req.qty * multiplier, 2)
    max_risk = _estimated_max_loss(req, ref_price, est_cost, multiplier, closing)
    intent = "close" if closing else "open"
    strategy_name = "manual_close" if closing else "manual_order"

    risk_proposal = RiskOrderProposal(
        strategy_name=strategy_name,
        legs=[req.symbol],
        symbol=req.symbol,
        asset_class=req.asset_class,
        side=req.side,
        qty=req.qty,
        max_risk_usd=max_risk,
        est_cost_usd=est_cost,
        signal_values={
            "intent": intent,
            "reference_price": ref_price,
            "order_type": req.order_type.value,
        },
        confidence=1.0,
        limit_price=req.limit_price,
        reason="manual_order_request",
        extended_hours=req.extended_hours,
    )
    config = await load_risk_config(equity=context.risk_account.equity)
    decision = RiskManager().evaluate_order(
        risk_proposal,
        context.risk_account,
        context.positions,
        context.state,
        config,
    )
    if not decision.approved:
        await _record_manual_event(
            "manual_order_rejected",
            broker.name,
            req,
            {
                "reasons": decision.reasons,
                "max_risk_usd": max_risk,
                "est_cost_usd": est_cost,
            },
            "warn",
        )
        raise HTTPException(403, {"reasons": decision.reasons})

    proposal = DomainOrderProposal(
        strategy_name=strategy_name,
        symbol=req.symbol,
        asset_class=req.asset_class,
        side=req.side,
        qty=req.qty,
        legs=[req.symbol],
        order_type=req.order_type,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        time_in_force=req.time_in_force,
        extended_hours=req.extended_hours,
        estimated_cost=est_cost,
        estimated_max_loss=max_risk,
        max_risk_usd=max_risk,
        est_cost_usd=est_cost,
        signal_values={
            "intent": intent,
            "reference_price": ref_price,
            "risk_reasons": decision.reasons,
        },
        confidence=1.0,
        reason="manual_order",
        risk_score=decision.risk_score,
    )
    try:
        order = await broker.place_order(proposal)
    except Exception as exc:
        await _record_manual_event(
            "manual_order_failed",
            broker.name,
            req,
            {"error": str(exc), "max_risk_usd": max_risk, "est_cost_usd": est_cost},
            "error",
        )
        raise HTTPException(400, str(exc)) from exc
    await record_broker_order(account=broker.name, proposal=proposal, order=order)
    await _record_manual_event(
        "manual_order_submitted",
        broker.name,
        req,
        {
            "order_id": order.id,
            "status": order.status.value,
            "max_risk_usd": max_risk,
            "est_cost_usd": est_cost,
            "risk_score": decision.risk_score,
            "intent": intent,
        },
        "warn" if broker.supports_live else "info",
    )
    return {
        **_order_dict(order, broker.name),
        "symbol": req.symbol,
        "side": req.side.value,
        "qty": req.qty,
        "asset_class": req.asset_class.value,
        "risk": {
            "approved": True,
            "risk_score": decision.risk_score,
            "max_risk_usd": max_risk,
            "est_cost_usd": est_cost,
            "intent": intent,
        },
    }


@router.delete("/{order_id}")
async def cancel_order(order_id: str) -> dict:
    broker = await get_broker()
    success = await broker.cancel_order(order_id)
    if not success:
        raise HTTPException(404, f"Order {order_id} not found or already final")
    await _mark_order_canceled(order_id)
    return {"status": "canceled", "order_id": order_id}


def _order_dict(o, broker_name: str | None = None) -> dict:
    raw = o.raw or {}
    return {
        "id":              o.id,
        "broker":          broker_name,
        "broker_environment": _broker_environment(broker_name),
        "status":          o.status.value,
        "filled_qty":      o.filled_qty,
        "avg_fill_price":  o.avg_fill_price,
        "symbol":          raw.get("symbol", ""),
        "side":            raw.get("side", ""),
        "qty":             raw.get("qty", ""),
        "order_type":      raw.get("order_type", raw.get("type", "")),
        "limit_price":     raw.get("limit_price", None),
        "stop_price":      raw.get("stop_price", None),
        "time_in_force":   raw.get("time_in_force", ""),
        "submitted_at":    raw.get("submitted_at", None),
        "filled_at":       raw.get("filled_at", None),
    }


def _broker_environment(broker_name: str | None) -> str:
    if broker_name == "alpaca_live":
        return "live"
    if broker_name == "alpaca_paper":
        return "paper"
    if broker_name == "paper":
        return "local_paper"
    return "unknown"


async def _mark_order_canceled(order_id: str) -> None:
    async with session_factory()() as session:
        res = await session.execute(
            select(OrderRow).where(OrderRow.broker_order_id == order_id).limit(1)
        )
        row = res.scalar_one_or_none()
        if row is None:
            res = await session.execute(select(OrderRow).where(OrderRow.id == order_id).limit(1))
            row = res.scalar_one_or_none()
        if row is None:
            return
        row.status = OrderStatus.CANCELED.value
        payload = dict(row.payload or {})
        fill = dict(payload.get("fill") or {})
        fill["status"] = OrderStatus.CANCELED.value
        payload["fill"] = fill
        payload["canceled_at"] = datetime.now(timezone.utc).isoformat()
        row.payload = payload
        await session.commit()


def _validate_manual_order(req: PlaceOrderRequest) -> None:
    if not req.symbol:
        raise HTTPException(400, "symbol is required")
    if (
        req.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}
        and req.limit_price is None
    ):
        raise HTTPException(400, f"{req.order_type.value} orders require limit_price")
    if (
        req.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}
        and req.stop_price is None
    ):
        raise HTTPException(400, f"{req.order_type.value} orders require stop_price")
    if req.limit_price is not None and req.limit_price <= 0:
        raise HTTPException(400, "limit_price must be positive")
    if req.stop_price is not None and req.stop_price <= 0:
        raise HTTPException(400, "stop_price must be positive")
    if req.extended_hours and req.asset_class != AssetClass.STOCK:
        raise HTTPException(400, "extended-hours manual orders are stock-only")
    if req.extended_hours and req.order_type != OrderType.LIMIT:
        raise HTTPException(400, "extended-hours manual orders must be limit orders")


async def _reference_price(req: PlaceOrderRequest, provider) -> float:
    if req.limit_price is not None:
        return req.limit_price
    if req.order_type == OrderType.STOP and req.stop_price is not None:
        return req.stop_price

    if req.asset_class == AssetClass.STOCK:
        quote = await provider.get_quote(req.symbol)
        price = quote.ask if req.side == Side.BUY else quote.bid
        price = price or quote.mid or quote.last
    else:
        contract = await provider.get_option_quote(req.symbol)
        price = contract.ask if req.side == Side.BUY else contract.bid
        price = price or contract.mid or contract.last
    if price <= 0:
        raise HTTPException(
            400,
            f"Could not estimate a usable reference price for {req.symbol}",
        )
    return round(float(price), 4)


def _estimated_max_loss(
    req: PlaceOrderRequest,
    ref_price: float,
    est_cost: float,
    multiplier: int,
    closing: bool,
) -> float:
    if closing:
        return 0.0
    if (
        req.side == Side.BUY
        and req.stop_price is not None
        and req.stop_price < ref_price
    ):
        return round((ref_price - req.stop_price) * req.qty * multiplier, 2)
    return est_cost


def _is_closing_order(req: PlaceOrderRequest, positions: list) -> bool:
    if req.side != Side.SELL:
        return False
    for position in positions:
        if (
            getattr(position, "symbol", "").upper() == req.symbol
            and getattr(position, "asset_class", None) == req.asset_class
            and int(getattr(position, "qty", 0) or 0) >= req.qty
        ):
            return True
    return False


async def _record_manual_event(
    kind: str,
    broker_name: str,
    req: PlaceOrderRequest,
    payload: dict,
    severity: str,
) -> None:
    async with session_factory()() as session:
        session.add(
            SystemEvent(
                kind=kind,
                message=(
                    f"{req.side.value.upper()} {req.symbol} "
                    f"x{req.qty} via {broker_name}"
                ),
                payload={
                    "broker": broker_name,
                    "symbol": req.symbol,
                    "side": req.side.value,
                    "qty": req.qty,
                    "asset_class": req.asset_class.value,
                    "order_type": req.order_type.value,
                    **payload,
                },
                severity=severity,
            )
        )
        await session.commit()
