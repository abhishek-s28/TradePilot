"""Paper broker.

Fully simulated. Fills limit orders if quote crosses, market orders at mid+slippage.
Tracks cash, positions, P&L, order history. State lives in DB via PaperEngine,
this adapter is the stateless execution surface.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.brokers.base import BrokerAdapter, BrokerOrder
from app.core.logging import get_logger
from app.data.factory import get_provider
from app.models.domain import (
    AccountSnapshot,
    AssetClass,
    OrderProposal,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from app.paper_trading.engine import PaperEngine

log = get_logger(__name__)

# Per-share slippage assumption for market orders
DEFAULT_SLIPPAGE_BPS = 5  # 5 basis points


class PaperBroker(BrokerAdapter):
    name = "paper"
    supports_live = False

    def __init__(self, engine: PaperEngine) -> None:
        self._engine = engine
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        log.info("paper_broker.connected")

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    async def is_market_open(self) -> bool:
        provider = await get_provider()
        return await provider.is_market_open()

    async def get_account(self) -> AccountSnapshot:
        return await self._engine.account_snapshot()

    async def get_positions(self) -> list[Position]:
        return await self._engine.positions()

    async def get_orders(self) -> list[BrokerOrder]:
        return await self._engine.orders()

    async def place_order(self, proposal: OrderProposal) -> BrokerOrder:
        provider = await get_provider()
        # Get reference price
        if proposal.asset_class == AssetClass.STOCK:
            quote = await provider.get_quote(proposal.symbol)
            ref_price = quote.mid
        else:
            contract = await provider.get_option_quote(proposal.symbol)
            ref_price = contract.mid

        fill_price = self._simulate_fill_price(proposal, ref_price)
        if fill_price is None:
            order_id = str(uuid.uuid4())
            await self._engine.record_order(
                order_id=order_id,
                proposal=proposal,
                status=OrderStatus.PENDING,
                filled_qty=0,
                avg_fill_price=None,
            )
            return BrokerOrder(
                id=order_id,
                status=OrderStatus.PENDING,
                filled_qty=0,
                avg_fill_price=None,
                raw={"reason": "limit_not_crossed", "ref_price": ref_price},
            )

        order_id = str(uuid.uuid4())
        await self._engine.fill_order(
            order_id=order_id,
            proposal=proposal,
            fill_price=fill_price,
            filled_at=datetime.now(timezone.utc),
        )
        return BrokerOrder(
            id=order_id,
            status=OrderStatus.FILLED,
            filled_qty=proposal.qty,
            avg_fill_price=fill_price,
            raw={"ref_price": ref_price},
        )

    async def cancel_order(self, order_id: str) -> bool:
        return await self._engine.cancel_order(order_id)

    async def close_position(self, symbol: str) -> BrokerOrder | None:
        pos = await self._engine.get_position(symbol)
        if not pos:
            return None
        provider = await get_provider()
        if pos.asset_class == AssetClass.STOCK:
            q = await provider.get_quote(symbol)
            price = q.mid
        else:
            c = await provider.get_option_quote(symbol)
            price = c.mid

        proposal = OrderProposal(
            symbol=symbol,
            asset_class=pos.asset_class,
            side=Side.SELL,
            qty=pos.qty,
            order_type=OrderType.MARKET,
            limit_price=None,
            estimated_cost=price * pos.qty,
            estimated_max_loss=0.0,
            reason="manual_close",
            risk_score=0.0,
        )
        return await self.place_order(proposal)

    # ── helpers ──
    @staticmethod
    def _simulate_fill_price(proposal: OrderProposal, ref_price: float) -> float | None:
        """Returns fill price or None if limit doesn't cross."""
        if proposal.order_type == OrderType.MARKET:
            slip = ref_price * DEFAULT_SLIPPAGE_BPS / 10_000
            return round(ref_price + slip if proposal.side == Side.BUY else ref_price - slip, 2)
        # LIMIT
        if proposal.limit_price is None:
            return None
        if proposal.side == Side.BUY and ref_price <= proposal.limit_price:
            return min(ref_price, proposal.limit_price)
        if proposal.side == Side.SELL and ref_price >= proposal.limit_price:
            return max(ref_price, proposal.limit_price)
        return None
