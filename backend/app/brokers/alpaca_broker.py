"""Alpaca broker adapter.

Defaults to Alpaca paper trading. Live Alpaca routing is only reachable when
the global live-trading gates are open and ALPACA_TRADING_PAPER=false.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from alpaca.common.exceptions import APIError

from app.brokers.base import BrokerAdapter, BrokerOrder
from app.core.http import harden_alpaca_client
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.domain import (
    AccountSnapshot,
    AssetClass,
    OrderProposal,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

log = get_logger(__name__)


class AlpacaBroker(BrokerAdapter):
    """Broker adapter backed by alpaca-py TradingClient."""

    def __init__(self, *, paper: bool = True) -> None:
        s = get_settings()
        s.validate_alpaca_credentials()
        self._key = s.alpaca_api_key.get_secret_value()
        self._secret = s.alpaca_api_secret.get_secret_value()
        self._paper = paper
        self._client = None
        self._connected = False
        self.name = "alpaca_paper" if paper else "alpaca_live"
        self.supports_live = not paper

    async def connect(self) -> None:
        from alpaca.trading.client import TradingClient

        url = (
            "https://paper-api.alpaca.markets"
            if self._paper
            else "https://api.alpaca.markets"
        )
        self._client = TradingClient(
            self._key,
            self._secret,
            paper=self._paper,
            url_override=url,
        )
        harden_alpaca_client(self._client)
        account_kind = "paper" if self._paper else "live"
        for attempt in range(1, 4):
            try:
                # Touch account once so startup proves credentials and base URL.
                await asyncio.to_thread(self._client.get_account)
                self._connected = True
                log.info("alpaca_broker.connected", paper=self._paper, url=url)
                return
            except APIError as exc:
                if _looks_auth_error(exc):
                    raise RuntimeError(
                        "Alpaca authentication failed. Check ALPACA_API_KEY and "
                        f"ALPACA_API_SECRET for the {account_kind} account; "
                        "refusing to continue."
                    ) from exc
                if attempt == 3:
                    raise RuntimeError(f"Alpaca connection failed after 3 attempts: {exc}") from exc
                await asyncio.sleep(2)
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    raise RuntimeError(f"Alpaca connection failed after 3 attempts: {exc}") from exc
                await asyncio.sleep(2)

    async def disconnect(self) -> None:
        self._connected = False
        self._client = None

    async def is_connected(self) -> bool:
        return self._connected

    async def is_market_open(self) -> bool:
        clock = await asyncio.to_thread(self._require_client().get_clock)
        return bool(clock.is_open)

    async def get_account(self) -> AccountSnapshot:
        acct = await asyncio.to_thread(self._require_client().get_account)
        equity = _to_float(getattr(acct, "equity", 0))
        last_equity = _to_float(getattr(acct, "last_equity", equity))
        positions = await self.get_positions()
        return AccountSnapshot(
            cash=_to_float(getattr(acct, "cash", 0)),
            equity=equity,
            buying_power=_to_float(getattr(acct, "buying_power", 0)),
            positions_value=sum(p.market_value for p in positions),
            daily_pnl=round(equity - last_equity, 2),
            open_positions=len(positions),
        )

    async def get_positions(self) -> list[Position]:
        rows = await asyncio.to_thread(self._require_client().get_all_positions)
        out: list[Position] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            qty = int(float(getattr(row, "qty", 0) or 0))
            avg_price = _to_float(getattr(row, "avg_entry_price", 0))
            current_price = _to_float(getattr(row, "current_price", avg_price))
            out.append(
                Position(
                    symbol=str(getattr(row, "symbol", "")),
                    asset_class=_asset_class(row),
                    qty=qty,
                    avg_price=avg_price,
                    current_price=current_price,
                    unrealized_pnl=_to_float(getattr(row, "unrealized_pl", 0)),
                    realized_pnl=0.0,
                    opened_at=now,
                )
            )
        return out

    async def get_orders(self) -> list[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=100)
        rows = await asyncio.to_thread(self._require_client().get_orders, filter=req)
        return [_order_from_alpaca(row) for row in rows]

    async def place_order(self, proposal: OrderProposal) -> BrokerOrder:
        order_req = _build_order_request(proposal)
        order = await asyncio.to_thread(
            self._require_client().submit_order,
            order_data=order_req,
        )
        log.info(
            "alpaca_order.submitted",
            paper=self._paper,
            symbol=proposal.symbol,
            qty=proposal.qty,
            order_type=proposal.order_type.value,
            strategy=proposal.strategy_name,
            max_risk_usd=proposal.max_risk_usd or proposal.estimated_max_loss,
            est_cost_usd=proposal.est_cost_usd or proposal.estimated_cost,
            signal_values=proposal.signal_values,
        )
        return _order_from_alpaca(order)

    async def place_options_order(
        self,
        occ_symbol: str,
        *,
        qty: int,
        side: Side = Side.BUY,
        limit_price: float | None = None,
        strategy_name: str = "",
        signal_values: dict | None = None,
        max_risk_usd: float = 0.0,
    ) -> BrokerOrder:
        if not _looks_like_occ(occ_symbol):
            raise ValueError(f"Expected OCC option symbol, got {occ_symbol}")
        proposal = OrderProposal(
            strategy_name=strategy_name,
            symbol=occ_symbol,
            asset_class=AssetClass.OPTION,
            side=side,
            qty=qty,
            legs=[occ_symbol],
            order_type=OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
            limit_price=limit_price,
            time_in_force=TimeInForce.DAY,
            estimated_cost=round((limit_price or 0.0) * qty * 100, 2),
            estimated_max_loss=max_risk_usd,
            max_risk_usd=max_risk_usd,
            est_cost_usd=round((limit_price or 0.0) * qty * 100, 2),
            signal_values=signal_values or {},
            confidence=float((signal_values or {}).get("confidence", 0.0) or 0.0),
            reason="direct_occ_option_order",
            risk_score=1.0 if max_risk_usd <= 0 else min(1.0, max_risk_usd / 25),
        )
        return await self.place_order(proposal)

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await asyncio.to_thread(self._require_client().cancel_order_by_id, order_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("alpaca_order.cancel_failed", order_id=order_id, error=str(exc))
            return False

    async def close_position(self, symbol: str) -> BrokerOrder | None:
        try:
            order = await asyncio.to_thread(self._require_client().close_position, symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("alpaca_position.close_failed", symbol=symbol, error=str(exc))
            return None
        return _order_from_alpaca(order)

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("Alpaca broker is not connected.")
        return self._client


def _build_order_request(proposal: OrderProposal):
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce as AlpacaTimeInForce
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        OptionLegRequest,
        StopLimitOrderRequest,
        StopOrderRequest,
    )

    side = OrderSide.BUY if proposal.side == Side.BUY else OrderSide.SELL
    tif = _time_in_force(proposal.time_in_force, AlpacaTimeInForce)
    if proposal.asset_class == AssetClass.OPTION and len(proposal.legs) > 1:
        if proposal.limit_price is None:
            raise ValueError("Multi-leg option orders require a net limit_price.")
        leg_sides = proposal.signal_values.get("leg_sides", {})
        legs = []
        for leg in proposal.legs:
            raw_side = str(leg_sides.get(leg, "buy")).lower()
            legs.append(
                OptionLegRequest(
                    symbol=leg,
                    ratio_qty=1,
                    side=OrderSide.SELL if raw_side == "sell" else OrderSide.BUY,
                )
            )
        return LimitOrderRequest(
            qty=proposal.qty,
            order_class=OrderClass.MLEG,
            time_in_force=tif,
            limit_price=proposal.limit_price,
            legs=legs,
        )

    base = {
        "symbol": proposal.symbol,
        "qty": proposal.qty,
        "side": side,
        "time_in_force": tif,
    }
    extended_hours = bool(
        proposal.extended_hours and proposal.asset_class == AssetClass.STOCK
    )

    if proposal.order_type == OrderType.MARKET:
        if extended_hours:
            raise ValueError("Alpaca extended-hours orders must be limit orders.")
        return MarketOrderRequest(**base)
    if proposal.order_type == OrderType.LIMIT:
        if proposal.limit_price is None:
            raise ValueError("Limit orders require limit_price.")
        kwargs = {**base, "limit_price": proposal.limit_price}
        if extended_hours:
            kwargs["extended_hours"] = True
        return LimitOrderRequest(**kwargs)
    if proposal.order_type == OrderType.STOP:
        if extended_hours:
            raise ValueError("Alpaca extended-hours orders must be limit orders.")
        if proposal.stop_price is None:
            raise ValueError("Stop orders require stop_price.")
        return StopOrderRequest(**base, stop_price=proposal.stop_price)
    if proposal.order_type == OrderType.STOP_LIMIT:
        if extended_hours:
            raise ValueError("Alpaca extended-hours orders must be limit orders.")
        if proposal.stop_price is None or proposal.limit_price is None:
            raise ValueError("Stop-limit orders require stop_price and limit_price.")
        return StopLimitOrderRequest(
            **base,
            stop_price=proposal.stop_price,
            limit_price=proposal.limit_price,
        )
    raise ValueError(f"Unsupported order type: {proposal.order_type}")


def _time_in_force(tif: TimeInForce, alpaca_tif):
    if tif == TimeInForce.GTC:
        return alpaca_tif.GTC
    if tif == TimeInForce.IOC:
        return alpaca_tif.IOC
    return alpaca_tif.DAY


def _order_from_alpaca(order: Any) -> BrokerOrder:
    return BrokerOrder(
        id=str(getattr(order, "id", "")),
        status=_order_status(getattr(order, "status", "")),
        filled_qty=int(float(getattr(order, "filled_qty", 0) or 0)),
        avg_fill_price=_optional_float(getattr(order, "filled_avg_price", None)),
        raw=_safe_raw(order),
    )


def _order_status(value: Any) -> OrderStatus:
    status = getattr(value, "value", value)
    status = str(status).lower()
    if status == "filled":
        return OrderStatus.FILLED
    if status == "partially_filled":
        return OrderStatus.PARTIALLY_FILLED
    if status in {"canceled", "cancelled", "expired"}:
        return OrderStatus.CANCELED
    if status in {"rejected", "stopped", "suspended"}:
        return OrderStatus.REJECTED
    if status in {"new", "accepted", "pending_new", "accepted_for_bidding"}:
        return OrderStatus.SUBMITTED
    return OrderStatus.PENDING


def _asset_class(row: Any) -> AssetClass:
    raw = getattr(row, "asset_class", "")
    value = str(getattr(raw, "value", raw)).lower()
    if "option" in value:
        return AssetClass.OPTION
    symbol = str(getattr(row, "symbol", ""))
    if len(symbol) > 8 and any(c.isdigit() for c in symbol):
        return AssetClass.OPTION
    return AssetClass.STOCK


def _safe_raw(obj: Any) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return {"repr": repr(obj)}


def _to_float(value: Any) -> float:
    return float(value or 0)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _looks_auth_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    text = str(exc).lower()
    return status in {401, 403} or "403" in text or "401" in text or "forbidden" in text


def _looks_like_occ(symbol: str) -> bool:
    return len(symbol) >= 15 and any(ch.isdigit() for ch in symbol[-15:])
