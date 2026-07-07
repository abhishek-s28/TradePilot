"""API smoke tests.

Boot the FastAPI app in-process via httpx.ASGITransport and hit the most
important endpoints. Not a substitute for proper integration tests, but
catches gross wiring breakage (missing routers, import cycles, JSON errors).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(db):
    # Import inside the fixture so the env vars + DB fixture are applied first.
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


async def test_market_status(client):
    r = await client.get("/market/status")
    assert r.status_code == 200
    body = r.json()
    assert body["data_provider"] == "mock"
    assert body["broker"] == "paper"
    assert body["can_trade_live"] is False
    assert "market_clock" in body


async def test_alpaca_status_missing_keys(client):
    r = await client.get("/integrations/alpaca/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["errors"]


async def test_strategies_list(client):
    r = await client.get("/strategies")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "momentum_breakout" in names
    assert "mean_reversion" in names


async def test_paper_account(client):
    r = await client.get("/paper/account")
    assert r.status_code == 200
    body = r.json()
    assert body["cash"] == 100_000.0


async def test_risk_settings_default(client):
    r = await client.get("/risk/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["kill_switch_active"] is False
    assert body["max_daily_loss_usd"] > 0


async def test_auto_status_refreshes_recent_trade_fill(client, monkeypatch):
    from app.api.v1 import auto_trading
    from app.brokers.base import BrokerOrder
    from app.database.models import RiskSettings, SystemEvent
    from app.database.session import session_factory
    from app.models.domain import OrderStatus

    class FakeBroker:
        async def get_orders(self):
            return [
                BrokerOrder(
                    id="order-1",
                    status=OrderStatus.FILLED,
                    filled_qty=1,
                    avg_fill_price=0.27,
                    raw={
                        "symbol": "XLF260626P00050500",
                        "side": "buy",
                        "qty": "1",
                        "order_type": "limit",
                        "limit_price": "0.27",
                        "submitted_at": "2026-06-11T17:34:32Z",
                        "filled_at": "2026-06-11T17:35:15Z",
                    },
                )
            ]

    async def fake_get_broker():
        return FakeBroker()

    monkeypatch.setattr(auto_trading, "get_broker", fake_get_broker)
    async with session_factory()() as s:
        s.add(RiskSettings(auto_trading_enabled=True, kill_switch_active=False))
        s.add(
            SystemEvent(
                kind="auto_trade",
                message="submitted order",
                payload={
                    "fill": {
                        "order_id": "order-1",
                        "status": "submitted",
                        "filled_qty": 0,
                        "avg_fill_price": None,
                    }
                },
            )
        )
        await s.commit()

    r = await client.get("/auto-trading/status")
    assert r.status_code == 200
    fill = r.json()["recent_trades"][0]["payload"]["fill"]
    assert fill["status"] == "filled"
    assert fill["filled_qty"] == 1
    assert fill["avg_fill_price"] == 0.27
    assert fill["filled_at"] == "2026-06-11T17:35:15Z"


async def test_risk_settings_update_caps_non_live_risk(client):
    r = await client.put(
        "/risk/settings",
        json={"max_daily_loss_usd": 2000.0, "allowed_tickers": ["AAPL"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["max_daily_loss_usd"] == 1500.0
    assert body["allowed_tickers"] == ["AAPL"]


async def test_kill_switch_toggle(client):
    r = await client.post("/risk/kill-switch?active=true")
    assert r.status_code == 200
    assert r.json()["kill_switch_active"] is True

    r = await client.post("/risk/kill-switch?active=false")
    assert r.status_code == 200
    assert r.json()["kill_switch_active"] is False


async def test_manual_order_respects_kill_switch(client):
    r = await client.post("/risk/kill-switch?active=true")
    assert r.status_code == 200

    r = await client.post(
        "/api/v1/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 1.0,
        },
    )
    assert r.status_code == 403
    assert "kill_switch_active" in r.json()["detail"]["reasons"]


async def test_manual_limit_order_is_risk_checked(client):
    r = await client.post(
        "/api/v1/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 1.0,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["risk"]["approved"] is True
    assert body["risk"]["max_risk_usd"] == 1.0


async def test_manual_naked_sell_is_blocked(client):
    r = await client.post(
        "/api/v1/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 1.0,
        },
    )
    assert r.status_code == 400
    assert "naked sell" in r.json()["detail"]


async def test_signals_scan_then_list(client):
    r = await client.post("/signals/scan", json={"universe": ["AAPL", "MSFT"]})
    assert r.status_code == 200
    body = r.json()
    assert "count" in body

    r = await client.get("/signals?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_options_chain(client):
    r = await client.get("/options/chain/AAPL")
    assert r.status_code == 200
    chain = r.json()
    assert chain
    assert all(c["underlying"] == "AAPL" for c in chain)


async def test_options_scan(client):
    r = await client.post(
        "/options/scan",
        json={
            "underlying": "AAPL",
            "min_open_interest": 0,
            "min_volume": 0,
            "max_spread_pct": 1.0,
            "min_dte": 1,
            "max_dte": 90,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "count" in body
    assert "results" in body


async def test_portfolio_empty(client):
    r = await client.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["cash"] == 100_000.0
    assert body["positions"] == []
