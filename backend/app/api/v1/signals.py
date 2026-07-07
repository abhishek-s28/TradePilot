"""Signal endpoints: list, scan, approve, paper-trade."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.brokers.factory import get_broker
from app.core.logging import get_logger
from app.data.factory import get_provider
from app.database.models import SignalRow
from app.database.session import session_factory
from app.market.session import classify_us_equity_session
from app.models.domain import (
    AssetClass,
    Direction,
    Signal,
    SignalStatus,
)
from app.risk.loader import load_risk_config
from app.risk.manager import RiskManager, RiskState
from app.services.signal_service import SignalService

log = get_logger(__name__)
router = APIRouter(prefix="/signals", tags=["signals"])


class ScanRequest(BaseModel):
    universe: list[str] | None = None
    include_options: bool = True


@router.get("")
async def list_signals(limit: int = 50) -> list[dict]:
    return await SignalService().list_recent(limit=limit)


@router.post("/scan")
async def scan(req: ScanRequest) -> dict:
    sigs = await SignalService().scan(
        req.universe,
        include_options_when_closed=req.include_options,
    )
    return {"count": len(sigs), "signals": [s.model_dump(mode="json") for s in sigs]}


@router.get("/{signal_id}")
async def get_signal(signal_id: str) -> dict:
    async with session_factory()() as s:
        res = await s.execute(select(SignalRow).where(SignalRow.id == signal_id))
        row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "signal not found")
    return _row_to_dict(row)


@router.post("/{signal_id}/paper-trade")
async def paper_trade_signal(signal_id: str) -> dict:
    sig_row, sig = await _load_signal(signal_id)
    if sig_row.status not in (SignalStatus.NEW.value, SignalStatus.APPROVED.value):
        raise HTTPException(409, f"signal status is {sig_row.status}")

    provider = await get_provider()
    quote = (
        await provider.get_quote(sig.symbol)
        if sig.asset_class == AssetClass.STOCK
        else None
    )
    if sig.asset_class == AssetClass.OPTION:
        contract = await provider.get_option_quote(sig.symbol)
        from app.models.domain import Quote as Q
        quote = Q(
            symbol=sig.symbol, bid=contract.bid, ask=contract.ask,
            last=contract.last, timestamp=datetime.now(timezone.utc),
        )

    broker = await get_broker()
    account = await broker.get_account()
    positions = await broker.get_positions()

    state = RiskState(
        daily_realized_pnl=account.daily_pnl,
        open_positions=positions,
        trades_today=0,
        consecutive_losses=0,
        market_session=classify_us_equity_session().session.value,
    )
    decision = RiskManager().evaluate_signal(
        signal=sig, quote=quote, account=account, state=state, config=await load_risk_config(equity=account.equity)
    )
    if not decision.approved:
        async with session_factory()() as s:
            res = await s.execute(select(SignalRow).where(SignalRow.id == signal_id))
            r = res.scalar_one()
            r.status = SignalStatus.REJECTED.value
            r.payload = {**(r.payload or {}), "rejection_reasons": decision.reasons}
            await s.commit()
        raise HTTPException(422, {"approved": False, "reasons": decision.reasons})

    order = await broker.place_order(decision.proposal)
    async with session_factory()() as s:
        res = await s.execute(select(SignalRow).where(SignalRow.id == signal_id))
        r = res.scalar_one()
        r.status = SignalStatus.PAPER_EXECUTED.value
        await s.commit()
    return {
        "approved": True,
        "order_id": order.id,
        "status": order.status.value,
        "filled_qty": order.filled_qty,
        "fill_price": order.avg_fill_price,
        "proposal": decision.proposal.model_dump(mode="json"),
    }


@router.post("/{signal_id}/reject")
async def reject_signal(signal_id: str) -> dict:
    async with session_factory()() as s:
        res = await s.execute(select(SignalRow).where(SignalRow.id == signal_id))
        row = res.scalar_one_or_none()
        if not row:
            raise HTTPException(404, "signal not found")
        row.status = SignalStatus.REJECTED.value
        await s.commit()
    return {"status": "rejected"}


# ── helpers ──
async def _load_signal(signal_id: str) -> tuple[SignalRow, Signal]:
    async with session_factory()() as s:
        res = await s.execute(select(SignalRow).where(SignalRow.id == signal_id))
        row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "signal not found")
    sig = Signal(
        id=row.id, strategy=row.strategy,
        asset_class=AssetClass(row.asset_class), symbol=row.symbol,
        underlying=row.underlying, direction=Direction(row.direction),
        entry=row.entry, stop_loss=row.stop_loss, take_profit=row.take_profit,
        confidence=row.confidence, reason=row.reason, invalidation=row.invalidation,
        risk_reward=row.risk_reward, suggested_qty=row.suggested_qty,
        suitable_for_options=row.suitable_for_options,
        holding_period_hint=row.holding_period_hint,
        generated_at=row.generated_at, status=SignalStatus(row.status),
        metadata=row.payload or {},
    )
    return row, sig


def _row_to_dict(r: SignalRow) -> dict:
    return {
        "id": r.id, "strategy": r.strategy, "asset_class": r.asset_class,
        "symbol": r.symbol, "underlying": r.underlying, "direction": r.direction,
        "entry": r.entry, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
        "confidence": r.confidence, "reason": r.reason, "invalidation": r.invalidation,
        "risk_reward": r.risk_reward, "suggested_qty": r.suggested_qty,
        "suitable_for_options": r.suitable_for_options,
        "status": r.status, "generated_at": r.generated_at.isoformat(),
        "metadata": r.payload,
    }
