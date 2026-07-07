"""Health & system status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from fastapi import APIRouter, Query

from app.brokers.factory import get_broker
from app.core.settings import get_settings
from app.data.factory import get_provider
from app.market.session import classify_us_equity_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/market/status")
async def market_status() -> dict:
    s = get_settings()
    provider = await get_provider()
    broker = await get_broker()
    try:
        clock = await provider.get_market_clock()
        market_open = bool(clock["is_open"])
    except Exception as exc:  # noqa: BLE001
        market_open = False
        clock = {
            "is_open": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "next_open": None,
            "next_close": None,
        }
        market_err = str(exc)
    else:
        market_err = None
    session_info = classify_us_equity_session()
    clock = {
        **clock,
        "session": session_info.session.value,
        "phase": session_info.phase.value,
        "equity_tradable": session_info.is_equity_tradable,
        "options_tradable": session_info.is_options_tradable,
        "extended_hours": session_info.allows_extended_hours,
    }
    return {
        "market_open": market_open,
        "equity_session_open": session_info.is_equity_tradable,
        "market_clock": clock,
        "data_provider": provider.name,
        "broker": broker.name,
        "broker_connected": await broker.is_connected(),
        "trading_mode": s.trading_mode.value,
        "live_trading_enabled": s.live_trading_enabled,
        "live_trading_unlocked": s.live_trading_unlocked,
        "can_trade_live": s.can_trade_live,
        "errors": {"market": market_err} if market_err else {},
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/integrations/alpaca/status")
async def alpaca_status(paper: bool | None = Query(default=None)) -> dict:
    s = get_settings()
    target_paper = s.alpaca_trading_paper if paper is None else paper
    key = s.alpaca_api_key.get_secret_value()
    secret = s.alpaca_api_secret.get_secret_value()
    configured = bool(key and secret)
    status = {
        "configured": configured,
        "data_feed": s.alpaca_data_feed,
        "trading_paper": target_paper,
        "trading_environment": "paper" if target_paper else "live",
        "configured_trading_paper": s.alpaca_trading_paper,
        "broker": s.broker,
        "can_trade_live": s.can_trade_live,
        "clock": None,
        "account": None,
        "errors": [],
    }
    if not configured:
        status["errors"].append(
            "Missing ALPACA_API_KEY or ALPACA_API_SECRET in backend/.env."
        )
        return status

    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(key, secret, paper=target_paper)
        clock, account = await asyncio.gather(
            asyncio.to_thread(client.get_clock),
            asyncio.to_thread(client.get_account),
        )
        status["clock"] = {
            "is_open": bool(clock.is_open),
            "timestamp": _iso(clock.timestamp),
            "next_open": _iso(clock.next_open),
            "next_close": _iso(clock.next_close),
        }
        status["account"] = {
            "status": str(getattr(account, "status", "")),
            "currency": str(getattr(account, "currency", "")),
            "equity": str(getattr(account, "equity", "")),
            "buying_power": str(getattr(account, "buying_power", "")),
            "trading_blocked": bool(getattr(account, "trading_blocked", False)),
            "account_blocked": bool(getattr(account, "account_blocked", False)),
            "options_approved_level": getattr(
                account,
                "options_approved_level",
                None,
            ),
            "options_trading_level": getattr(
                account,
                "options_trading_level",
                None,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        status["errors"].append(str(exc))
    return status


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
