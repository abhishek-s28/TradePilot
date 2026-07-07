"""Interactive Brokers adapter (STUB — Phase 4).

Structure is ready; real ib_insync calls will be wired in Phase 4 alongside
the TWS/Gateway connection lifecycle, contract resolution, and order routing.

This stub deliberately RAISES when live trading is attempted, so the rest of
the system can already depend on the BrokerAdapter shape without risk.
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter, BrokerOrder
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.domain import AccountSnapshot, OrderProposal, Position

log = get_logger(__name__)


class NotYetEnabledError(RuntimeError):
    pass


class IBKRBroker(BrokerAdapter):
    name = "ibkr"
    supports_live = True

    def __init__(self) -> None:
        self._connected = False
        self._settings = get_settings()

    async def connect(self) -> None:
        # Real implementation (Phase 4):
        #   from ib_insync import IB
        #   self._ib = IB()
        #   await self._ib.connectAsync(host, port, clientId)
        log.warning(
            "ibkr.connect_stub",
            note="IBKR adapter not implemented yet. Phase 4 will wire ib_insync.",
        )
        raise NotYetEnabledError(
            "IBKR adapter not yet implemented. Use BROKER=paper for now."
        )

    async def disconnect(self) -> None: self._connected = False
    async def is_connected(self) -> bool: return self._connected
    async def is_market_open(self) -> bool: return False
    async def get_account(self) -> AccountSnapshot: raise NotYetEnabledError("ibkr stub")
    async def get_positions(self) -> list[Position]: raise NotYetEnabledError("ibkr stub")
    async def get_orders(self) -> list[BrokerOrder]: raise NotYetEnabledError("ibkr stub")
    async def place_order(self, proposal: OrderProposal) -> BrokerOrder:
        raise NotYetEnabledError("ibkr stub — Phase 4 will implement this safely")
    async def cancel_order(self, order_id: str) -> bool: raise NotYetEnabledError("ibkr stub")
    async def close_position(self, symbol: str) -> BrokerOrder | None:
        raise NotYetEnabledError("ibkr stub")
