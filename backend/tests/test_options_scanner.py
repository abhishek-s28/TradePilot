"""Options scanner tests using the mock provider."""
from __future__ import annotations

import pytest

from app.data.factory import shutdown_provider
from app.data.mock_provider import MockMarketDataProvider
from app.models.domain import OptionRight
from app.options.scanner import OptionsFilter, scan_chain


@pytest.fixture(autouse=True)
async def _provider(monkeypatch):
    p = MockMarketDataProvider()
    await p.connect()

    async def _get():
        return p

    monkeypatch.setattr("app.data.factory.get_provider", _get)
    monkeypatch.setattr("app.options.scanner.get_provider", _get)
    yield
    await p.disconnect()


async def test_scan_returns_results():
    results = await scan_chain("AAPL", OptionsFilter(min_open_interest=0, min_volume=0))
    assert results
    assert all(r["underlying"] == "AAPL" for r in results)


async def test_filter_by_right():
    calls = await scan_chain(
        "AAPL", OptionsFilter(min_open_interest=0, min_volume=0, right=OptionRight.CALL)
    )
    assert calls
    assert all(r["right"] == "call" for r in calls)


async def test_filter_by_dte():
    results = await scan_chain(
        "AAPL", OptionsFilter(min_open_interest=0, min_volume=0, min_dte=1, max_dte=10)
    )
    assert all(1 <= r["dte"] <= 10 for r in results)


async def test_filter_by_spread():
    results = await scan_chain(
        "AAPL",
        OptionsFilter(min_open_interest=0, min_volume=0, max_spread_pct=0.05),
    )
    assert all(r["spread_pct"] <= 0.05 for r in results)


async def test_results_sorted_by_liquidity():
    results = await scan_chain("AAPL", OptionsFilter(min_open_interest=0, min_volume=0))
    scores = [r["liquidity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
