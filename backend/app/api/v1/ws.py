"""WebSocket endpoints for real-time UI updates."""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.brokers.factory import get_broker
from app.core.logging import get_logger
from app.data.factory import get_provider
from app.services.signal_service import SignalService

log = get_logger(__name__)
router = APIRouter()

# Default symbols to stream quotes for when client doesn't specify
_DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
    "GOOGL", "META", "SPY", "QQQ", "AMD",
]


class WSManager:
    def __init__(self) -> None:
        self._conns: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        msg = json.dumps(payload, default=str)
        async with self._lock:
            conns = list(self._conns)
        for ws in conns:
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = WSManager()


@router.websocket("/ws/market")
async def market_ws(ws: WebSocket) -> None:
    """Streams live quotes + account stats to the frontend every 2 seconds."""
    await manager.connect(ws)
    symbols = list(_DEFAULT_SYMBOLS)
    try:
        while True:
            provider = await get_provider()
            broker   = await get_broker()

            # Fetch quotes + account in parallel
            quotes_task  = provider.get_quotes(symbols)
            account_task = broker.get_account()
            quotes, account = await asyncio.gather(
                quotes_task, account_task, return_exceptions=True
            )

            payload: dict = {"type": "market_update"}

            if isinstance(quotes, dict):
                payload["quotes"] = {
                    sym: {
                        "bid":    q.bid,
                        "ask":    q.ask,
                        "last":   q.last,
                        "mid":    q.mid,
                        "ts":     q.timestamp.isoformat(),
                    }
                    for sym, q in quotes.items()
                }
            else:
                payload["quotes"] = {}

            if not isinstance(account, Exception):
                payload["account"] = {
                    "equity":          account.equity,
                    "cash":            account.cash,
                    "buying_power":    account.buying_power,
                    "positions_value": account.positions_value,
                    "daily_pnl":       account.daily_pnl,
                    "open_positions":  account.open_positions,
                }

            await ws.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("market_ws.error", error=str(exc))
    finally:
        await manager.disconnect(ws)


@router.websocket("/ws/system")
async def system_ws(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            broker  = await get_broker()
            account = await broker.get_account()
            payload = {
                "type":             "system",
                "broker":           broker.name,
                "broker_connected": await broker.is_connected(),
                "account":          account.model_dump(),
            }
            await ws.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)


@router.websocket("/ws/signals")
async def signals_ws(ws: WebSocket) -> None:
    await manager.connect(ws)
    svc     = SignalService()
    last_ids: set[str] = set()
    try:
        while True:
            recent = await svc.list_recent(limit=20)
            new    = [r for r in recent if r["id"] not in last_ids]
            if new:
                last_ids.update(r["id"] for r in new)
                await ws.send_text(
                    json.dumps({"type": "signals", "items": new}, default=str)
                )
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)


@router.websocket("/ws/portfolio")
async def portfolio_ws(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            broker    = await get_broker()
            account   = await broker.get_account()
            positions = [p.model_dump() for p in await broker.get_positions()]
            await ws.send_text(json.dumps(
                {"type": "portfolio", "account": account.model_dump(), "positions": positions},
                default=str,
            ))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)


async def cleanup_ws_manager() -> None:
    async with manager._lock:
        conns = list(manager._conns)
    for ws in conns:
        with suppress(Exception):
            await ws.close()
