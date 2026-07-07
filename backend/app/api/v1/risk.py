"""Risk settings endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.database.models import AuditLog, RiskSettings
from app.database.session import session_factory
from app.risk.loader import non_live_risk_caps

log = get_logger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])


class RiskUpdate(BaseModel):
    max_daily_loss_usd: float | None = None
    max_trade_loss_usd: float | None = None
    max_open_positions: int | None = None
    max_trades_per_day: int | None = None
    max_option_premium_usd: float | None = None
    cooldown_after_losses: int | None = None
    allowed_strategies: list[str] | None = None
    allowed_tickers: list[str] | None = None
    auto_trading_enabled: bool | None = None


async def _get_or_create() -> RiskSettings:
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return row


@router.get("/settings")
async def get_settings_endpoint() -> dict:
    r = await _get_or_create()
    return {
        "max_daily_loss_usd": r.max_daily_loss_usd,
        "max_trade_loss_usd": r.max_trade_loss_usd,
        "max_open_positions": r.max_open_positions,
        "max_trades_per_day": r.max_trades_per_day,
        "max_option_premium_usd": r.max_option_premium_usd,
        "cooldown_after_losses": r.cooldown_after_losses,
        "allowed_strategies": r.allowed_strategies or [],
        "allowed_tickers": r.allowed_tickers or [],
        "kill_switch_active": r.kill_switch_active,
        "auto_trading_enabled": r.auto_trading_enabled,
    }


@router.put("/settings")
async def update_settings(u: RiskUpdate) -> dict:
    changes = u.model_dump(exclude_unset=True)
    for key, cap in non_live_risk_caps(get_settings()).items():
        if key in changes and changes[key] is not None:
            changes[key] = min(changes[key], cap)

    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.flush()
        for k, v in changes.items():
            setattr(row, k, v)
        s.add(AuditLog(actor="user", action="risk.update", target=row.id, detail=changes))
        await s.commit()
    log.info("risk.settings_updated", changes=changes)
    return await get_settings_endpoint()


@router.post("/kill-switch")
async def kill_switch(active: bool = True) -> dict:
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings()
            s.add(row)
            await s.flush()
        row.kill_switch_active = active
        if active:
            row.auto_trading_enabled = False
        s.add(AuditLog(
            actor="user", action="risk.kill_switch", target=row.id,
            detail={"active": active}, severity="critical" if active else "warn",
        ))
        await s.commit()
    log.warning("risk.kill_switch", active=active)
    return {"kill_switch_active": active}
