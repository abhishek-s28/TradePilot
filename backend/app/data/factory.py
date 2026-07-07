"""Provider factory + singleton."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.data.base import MarketDataProvider
from app.data.mock_provider import MockMarketDataProvider

log = get_logger(__name__)

_provider: MarketDataProvider | None = None


async def get_provider() -> MarketDataProvider:
    """Singleton provider. Configured by DATA_PROVIDER env."""
    global _provider
    if _provider is not None:
        return _provider

    s = get_settings()
    if s.data_provider == "alpaca":
        try:
            from app.data.alpaca_provider import AlpacaMarketDataProvider

            _provider = AlpacaMarketDataProvider()
            log.info("data_provider.selected", provider="alpaca")
        except Exception as exc:
            log.error("data_provider.alpaca_failed_falling_back_to_yahoo", error=str(exc))
            from app.data.yahoo_provider import YahooFinanceProvider
            _provider = YahooFinanceProvider()
    elif s.data_provider == "yahoo":
        from app.data.yahoo_provider import YahooFinanceProvider
        _provider = YahooFinanceProvider()
        log.info("data_provider.selected", provider="yahoo")
    else:
        _provider = MockMarketDataProvider()
        log.info("data_provider.selected", provider="mock")

    await _provider.connect()
    return _provider


async def shutdown_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.disconnect()
        _provider = None
