"""Performance analytics API.

GET /analytics/stats         — Sharpe, win rate, avg R/R, max drawdown, totals
GET /analytics/journal       — closed trade journal (entry/exit/P&L per trade)
GET /analytics/equity-curve  — time-series equity for charting
GET /analytics/daily-pnl     — day-by-day realized P&L breakdown
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.database.models import OrderRow, PaperAccount, PositionRow, SystemEvent
from app.database.session import session_factory

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/journal")
async def trade_journal(limit: int = 200) -> list[dict]:
    """All closed paper positions with entry, exit price and realized P&L."""
    async with session_factory()() as s:
        res = await s.execute(
            select(PositionRow)
            .where(
                PositionRow.account == "paper",
                PositionRow.closed_at.is_not(None),
            )
            .order_by(PositionRow.closed_at.desc())
            .limit(limit)
        )
        rows = res.scalars().all()

    trades = []
    for r in rows:
        pnl = r.realized_pnl
        mult = 100 if r.asset_class == "option" else 1
        cost_basis = r.avg_price * r.qty * mult
        pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0.0
        duration_min: float | None = None
        if r.opened_at and r.closed_at:
            duration_min = round(
                (r.closed_at - r.opened_at).total_seconds() / 60, 1
            )
        trades.append({
            "id": r.id,
            "symbol": r.symbol,
            "asset_class": r.asset_class,
            "qty": r.qty,
            "avg_entry": round(r.avg_price, 4),
            "realized_pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
            "duration_minutes": duration_min,
            "stop_loss": r.stop_loss,
            "take_profit": r.take_profit,
            "outcome": "win" if pnl > 0 else ("loss" if pnl < 0 else "flat"),
        })
    return trades


@router.get("/stats")
async def performance_stats() -> dict:
    """Aggregate performance statistics: win rate, Sharpe, drawdown, etc."""
    async with session_factory()() as s:
        pos_res = await s.execute(
            select(PositionRow).where(
                PositionRow.account == "paper",
                PositionRow.closed_at.is_not(None),
            )
        )
        closed = pos_res.scalars().all()

        acct_res = await s.execute(select(PaperAccount).limit(1))
        acct = acct_res.scalar_one_or_none()

    if not closed:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "starting_cash": acct.starting_cash if acct else 100_000.0,
            "current_equity": acct.cash if acct else 100_000.0,
            "total_return_pct": 0.0,
        }

    pnls = [r.realized_pnl for r in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = abs(sum(wins) / sum(losses)) if losses else float("inf")

    # Sharpe (daily, annualized) — approximate from trade P&L series
    if len(pnls) >= 3:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
        std_pnl = math.sqrt(variance) if variance > 0 else 1.0
        sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown — running equity curve from starting cash
    starting = acct.starting_cash if acct else 100_000.0
    equity = starting
    peak = starting
    max_dd_pct = 0.0
    # Sort by closed_at to get chronological order
    sorted_pnls = [r.realized_pnl for r in sorted(closed, key=lambda r: r.closed_at or datetime.min)]
    for pnl in sorted_pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    total_return_pct = (total_pnl / starting * 100) if starting > 0 else 0.0

    return {
        "total_trades": len(pnls),
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "avg_pnl": round(sum(pnls) / len(pnls), 2),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else 999.0,
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd_pct * 100, 2),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "starting_cash": starting,
        "total_return_pct": round(total_return_pct, 2),
    }


@router.get("/equity-curve")
async def equity_curve() -> list[dict]:
    """Chronological equity snapshots for charting."""
    async with session_factory()() as s:
        pos_res = await s.execute(
            select(PositionRow)
            .where(
                PositionRow.account == "paper",
                PositionRow.closed_at.is_not(None),
            )
            .order_by(PositionRow.closed_at.asc())
        )
        closed = pos_res.scalars().all()

        acct_res = await s.execute(select(PaperAccount).limit(1))
        acct = acct_res.scalar_one_or_none()

    starting = acct.starting_cash if acct else 100_000.0
    equity = starting
    curve = [{"time": None, "equity": round(starting, 2), "trade": 0, "pnl": 0.0}]
    for i, r in enumerate(closed, 1):
        equity += r.realized_pnl
        curve.append({
            "time": r.closed_at.isoformat() if r.closed_at else None,
            "equity": round(equity, 2),
            "trade": i,
            "pnl": round(r.realized_pnl, 2),
            "symbol": r.symbol,
            "outcome": "win" if r.realized_pnl > 0 else "loss",
        })
    return curve


@router.get("/daily-pnl")
async def daily_pnl() -> list[dict]:
    """Day-by-day realized P&L for the bar chart."""
    async with session_factory()() as s:
        pos_res = await s.execute(
            select(PositionRow)
            .where(
                PositionRow.account == "paper",
                PositionRow.closed_at.is_not(None),
            )
            .order_by(PositionRow.closed_at.asc())
        )
        closed = pos_res.scalars().all()

    daily: dict[str, float] = {}
    for r in closed:
        if r.closed_at:
            day = r.closed_at.strftime("%Y-%m-%d")
            daily[day] = daily.get(day, 0.0) + r.realized_pnl

    return [
        {"date": day, "pnl": round(pnl, 2), "positive": pnl >= 0}
        for day, pnl in sorted(daily.items())
    ]
