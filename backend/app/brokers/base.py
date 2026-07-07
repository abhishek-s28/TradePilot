"""Broker adapter interface.

Every broker (paper, IBKR, Alpaca trading, etc.) implements this same shape.
The rest of the system NEVER imports a specific broker — only this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.domain import (
    AccountSnapshot,
    OrderProposal,
    OrderStatus,
    Position,
)


@dataclass
class BrokerOrder:
    id: str
    status: OrderStatus
    filled_qty: int
    avg_fill_price: float | None
    raw: dict


class BrokerAdapter(ABC):
    name: str = "abstract"
    supports_live: bool = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def is_connected(self) -> bool: ...

    @abstractmethod
    async def is_market_open(self) -> bool: ...

    @abstractmethod
    async def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_orders(self) -> list[BrokerOrder]: ...

    @abstractmethod
    async def place_order(self, proposal: OrderProposal) -> BrokerOrder: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def close_position(self, symbol: str) -> BrokerOrder | None: ...
