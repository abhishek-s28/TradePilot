"""Paper trading engine.

Owns the simulated account, positions, and fill bookkeeping in PostgreSQL.
The PaperBroker calls into this; this is the source of truth for paper P&L.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.brokers.base import BrokerOrder
from app.core.logging import get_logger
from app.data.factory import get_provider
from app.database.models import OrderRow, PaperAccount, PositionRow
from app.database.session import session_factory
from app.models.domain import (
    AccountSnapshot,
    AssetClass,
    OrderProposal,
    OrderStatus,
    Position,
    Side,
)

log = get_logger(__name__)
DEFAULT_STARTING_CASH = 100_000.0


class PaperEngine:
    """Persistent paper-trading state. One singleton account for now."""

    async def ensure_account(self) -> str:
        async with session_factory()() as s:
            res = await s.execute(select(PaperAccount).limit(1))
            acct = res.scalar_one_or_none()
            if acct is None:
                acct = PaperAccount(
                    starting_cash=DEFAULT_STARTING_CASH,
                    cash=DEFAULT_STARTING_CASH,
                )
                s.add(acct)
                await s.commit()
                await s.refresh(acct)
            return acct.id

    async def reset(self, starting_cash: float = DEFAULT_STARTING_CASH) -> None:
        async with session_factory()() as s:
            await s.execute(PositionRow.__table__.delete().where(PositionRow.account == "paper"))
            await s.execute(OrderRow.__table__.delete().where(OrderRow.account == "paper"))
            await s.execute(PaperAccount.__table__.delete())
            s.add(PaperAccount(starting_cash=starting_cash, cash=starting_cash))
            await s.commit()
        log.info("paper.reset", cash=starting_cash)

    async def account_snapshot(self) -> AccountSnapshot:
        positions = await self.positions()
        positions_value = sum(p.market_value for p in positions)
        async with session_factory()() as s:
            res = await s.execute(select(PaperAccount).limit(1))
            acct = res.scalar_one_or_none()
            cash = acct.cash if acct else DEFAULT_STARTING_CASH
            realized = acct.realized_pnl if acct else 0.0
        equity = cash + positions_value
        # Buying power: cash for paper (no margin for now)
        return AccountSnapshot(
            cash=round(cash, 2),
            equity=round(equity, 2),
            buying_power=round(cash, 2),
            positions_value=round(positions_value, 2),
            daily_pnl=round(realized, 2),
            open_positions=len(positions),
        )

    async def positions(self) -> list[Position]:
        provider = await get_provider()
        out: list[Position] = []
        async with session_factory()() as s:
            res = await s.execute(
                select(PositionRow).where(
                    PositionRow.account == "paper",
                    PositionRow.closed_at.is_(None),
                )
            )
            rows = res.scalars().all()
        # batch quotes for stocks
        stock_syms = [r.symbol for r in rows if r.asset_class == AssetClass.STOCK.value]
        stock_quotes = await provider.get_quotes(stock_syms) if stock_syms else {}
        for r in rows:
            if r.asset_class == AssetClass.STOCK.value:
                q = stock_quotes.get(r.symbol)
                current = q.mid if q else r.avg_price
            else:
                contract = await provider.get_option_quote(r.symbol)
                current = contract.mid or r.avg_price
            mult = 100 if r.asset_class == AssetClass.OPTION.value else 1
            unrealized = (current - r.avg_price) * r.qty * mult
            out.append(
                Position(
                    symbol=r.symbol,
                    asset_class=AssetClass(r.asset_class),
                    qty=r.qty,
                    avg_price=r.avg_price,
                    current_price=current,
                    unrealized_pnl=round(unrealized, 2),
                    realized_pnl=r.realized_pnl,
                    opened_at=r.opened_at,
                )
            )
        return out

    async def get_position(self, symbol: str) -> Position | None:
        async with session_factory()() as s:
            res = await s.execute(
                select(PositionRow).where(
                    PositionRow.account == "paper",
                    PositionRow.symbol == symbol,
                    PositionRow.closed_at.is_(None),
                ).limit(1)
            )
            row = res.scalar_one_or_none()
        if not row:
            return None
        # build via positions() to get current price
        all_pos = await self.positions()
        for p in all_pos:
            if p.symbol == symbol:
                return p
        return None

    async def orders(self) -> list[BrokerOrder]:
        async with session_factory()() as s:
            res = await s.execute(
                select(OrderRow).where(OrderRow.account == "paper").order_by(OrderRow.created_at.desc())
            )
            rows = res.scalars().all()
        return [
            BrokerOrder(
                id=r.id,
                status=OrderStatus(r.status),
                filled_qty=r.filled_qty,
                avg_fill_price=r.avg_fill_price,
                raw=r.payload or {},
            )
            for r in rows
        ]

    async def record_order(
        self,
        order_id: str,
        proposal: OrderProposal,
        status: OrderStatus,
        filled_qty: int,
        avg_fill_price: float | None,
    ) -> None:
        async with session_factory()() as s:
            s.add(
                OrderRow(
                    id=order_id,
                    signal_id=proposal.signal_id,
                    account="paper",
                    symbol=proposal.symbol,
                    asset_class=proposal.asset_class.value,
                    side=proposal.side.value,
                    qty=proposal.qty,
                    order_type=proposal.order_type.value,
                    limit_price=proposal.limit_price,
                    stop_price=proposal.stop_price,
                    status=status.value,
                    filled_qty=filled_qty,
                    avg_fill_price=avg_fill_price,
                    submitted_at=datetime.now(timezone.utc),
                    payload={
                        "reason": proposal.reason,
                        "risk_score": proposal.risk_score,
                        "stop_loss": proposal.stop_loss,
                        "take_profit": proposal.take_profit,
                        "extended_hours": proposal.extended_hours,
                    },
                )
            )
            await s.commit()

    async def fill_order(
        self,
        order_id: str,
        proposal: OrderProposal,
        fill_price: float,
        filled_at: datetime,
    ) -> None:
        await self.record_order(
            order_id, proposal, OrderStatus.FILLED, proposal.qty, fill_price
        )
        async with session_factory()() as s:
            # update cash & position
            acct_res = await s.execute(select(PaperAccount).limit(1))
            acct = acct_res.scalar_one()
            mult = 100 if proposal.asset_class == AssetClass.OPTION else 1
            notional = fill_price * proposal.qty * mult

            if proposal.side == Side.BUY:
                acct.cash -= notional
                # open or add to position
                pos_res = await s.execute(
                    select(PositionRow).where(
                        PositionRow.account == "paper",
                        PositionRow.symbol == proposal.symbol,
                        PositionRow.closed_at.is_(None),
                    )
                )
                pos = pos_res.scalar_one_or_none()
                if pos:
                    new_qty = pos.qty + proposal.qty
                    pos.avg_price = (pos.avg_price * pos.qty + fill_price * proposal.qty) / new_qty
                    pos.qty = new_qty
                else:
                    s.add(
                        PositionRow(
                            account="paper",
                            symbol=proposal.symbol,
                            asset_class=proposal.asset_class.value,
                            qty=proposal.qty,
                            avg_price=fill_price,
                            opened_at=filled_at,
                            stop_loss=proposal.stop_loss,
                            take_profit=proposal.take_profit,
                        )
                    )
            else:  # SELL
                acct.cash += notional
                pos_res = await s.execute(
                    select(PositionRow).where(
                        PositionRow.account == "paper",
                        PositionRow.symbol == proposal.symbol,
                        PositionRow.closed_at.is_(None),
                    )
                )
                pos = pos_res.scalar_one_or_none()
                if pos:
                    realized = (fill_price - pos.avg_price) * min(proposal.qty, pos.qty) * mult
                    pos.realized_pnl += realized
                    acct.realized_pnl += realized
                    pos.qty -= proposal.qty
                    if pos.qty <= 0:
                        pos.closed_at = filled_at
            await s.commit()
        log.info(
            "paper.fill",
            symbol=proposal.symbol,
            side=proposal.side.value,
            qty=proposal.qty,
            price=fill_price,
        )

    async def cancel_order(self, order_id: str) -> bool:
        async with session_factory()() as s:
            res = await s.execute(select(OrderRow).where(OrderRow.id == order_id))
            row = res.scalar_one_or_none()
            if not row or row.status != OrderStatus.PENDING.value:
                return False
            row.status = OrderStatus.CANCELED.value
            await s.commit()
        return True
