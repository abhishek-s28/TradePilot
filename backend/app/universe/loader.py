"""Live US equity universe.

Alpaca is the source of truth for tradable/shortable/optionable flags. Nasdaq
Trader supplies listing and ETF flags; SEC company_tickers adds CIK metadata.
If all network sources fail, the fallback seed is US-only and intentionally
small.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import aiohttp

from app.core.logging import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)

ALPACA_ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Symbols always included regardless of live-universe filters. Sorted by
# options liquidity tier: index ETFs first, then sector ETFs, then mega-caps.
CORE_OPTIONABLE_SYMBOLS: list[str] = [
    # ── Index & broad-market ETFs (highest options liquidity) ─────────────────
    "SPY", "QQQ", "IWM", "DIA", "SPX",
    # ── Volatility instruments ────────────────────────────────────────────────
    "VXX", "UVXY", "SVXY",
    # ── Leveraged index ETFs ──────────────────────────────────────────────────
    "TQQQ", "SQQQ", "SPXL", "SPXS", "TNA", "TZA", "DOG", "DDM", "DXD",
    # ── Sector ETFs ───────────────────────────────────────────────────────────
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE",
    "SMH", "SOXX", "XBI", "IBB", "ARKK", "GDX", "GDXJ",
    # ── Bond & rates ETFs ─────────────────────────────────────────────────────
    "TLT", "HYG", "LQD", "IEF", "SHY", "TMF", "TBT",
    # ── Commodity ETFs ────────────────────────────────────────────────────────
    "GLD", "SLV", "USO", "UNG", "IBIT", "GBTC",
    # ── Mega-cap tech ─────────────────────────────────────────────────────────
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AMD",
    "AVGO", "ORCL", "CRM", "ADBE", "INTC", "MU", "QCOM", "AMAT", "LRCX",
    # ── High-options-volume individual stocks ─────────────────────────────────
    "JPM", "BAC", "GS", "MS", "C", "WFC", "V", "MA", "PYPL", "SQ",
    "UNH", "JNJ", "PFE", "MRNA", "ABBV", "LLY",
    "XOM", "CVX", "SLB", "OXY",
    "NFLX", "DIS", "COIN", "HOOD", "MSTR",
    "PLTR", "RIVN", "NIO", "F", "GM",
    "BABA", "JD", "PDD",
]

FALLBACK_SEED: list[str] = CORE_OPTIONABLE_SYMBOLS + [
    "COST", "WMT", "TGT", "AMGN", "GILD", "REGN",
    "CAT", "DE", "MMM", "HON", "GE", "BA", "LMT", "RTX",
    "SBUX", "MCD", "NKE", "LULU",
    "SNOW", "DDOG", "ZS", "CRWD", "PANW", "NET", "SHOP", "UBER", "LYFT",
    "ZM", "ROKU", "SNAP", "PINS", "TWTR",
]

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class AssetRecord:
    symbol: str
    name: str = ""
    exchange: str = ""
    tradable: bool = False
    shortable: bool = False
    optionable: bool = False
    fractionable: bool = False
    etf: bool = False
    cik: str | None = None
    source: str = "live"


async def load_universe(force_refresh: bool = False) -> list[AssetRecord]:
    cache_path = _CACHE_DIR / f"universe_{date.today().isoformat()}.json"
    if cache_path.exists() and not force_refresh:
        return [AssetRecord(**row) for row in json.loads(cache_path.read_text())]

    try:
        alpaca, nasdaq, other, sec = await asyncio.gather(
            _fetch_alpaca_assets(),
            _fetch_text(NASDAQ_LISTED_URL),
            _fetch_text(OTHER_LISTED_URL),
            _fetch_sec_tickers(),
            return_exceptions=True,
        )
        if isinstance(alpaca, Exception):
            raise alpaca

        listings = {}
        if not isinstance(nasdaq, Exception):
            listings.update(_parse_nasdaq_listed(nasdaq))
        if not isinstance(other, Exception):
            listings.update(_parse_other_listed(other))
        sec_map = {} if isinstance(sec, Exception) else sec

        records = []
        for item in alpaca:
            symbol = str(item.get("symbol", "")).upper()
            if not symbol or "." in symbol:
                continue
            listing = listings.get(symbol, {})
            sec_meta = sec_map.get(symbol, {})
            records.append(AssetRecord(
                symbol=symbol,
                name=str(item.get("name") or listing.get("name") or sec_meta.get("title") or ""),
                exchange=str(item.get("exchange") or listing.get("exchange") or ""),
                tradable=bool(item.get("tradable")),
                shortable=bool(item.get("shortable")),
                optionable=bool(item.get("options_enabled") or item.get("optionable")),
                fractionable=bool(item.get("fractionable")),
                etf=bool(listing.get("etf", False)),
                cik=str(sec_meta["cik_str"]) if sec_meta.get("cik_str") is not None else None,
                source="alpaca+nasdaq+sec",
            ))

        if not records:
            raise RuntimeError("live universe returned zero records")

        cache_path.write_text(json.dumps([asdict(r) for r in records]))
        log.info("universe.loaded", records=len(records), cache=str(cache_path))
        return records
    except Exception as exc:  # noqa: BLE001
        log.warning("universe.live_sources_failed_using_fallback", error=str(exc))
        return [
            AssetRecord(symbol=s, tradable=True, shortable=True, optionable=True, source="fallback")
            for s in FALLBACK_SEED
        ]


async def tradable_equities() -> list[AssetRecord]:
    records = await load_universe()
    return [r for r in records if r.tradable]


async def etfs() -> list[AssetRecord]:
    records = await load_universe()
    return [r for r in records if r.tradable and r.etf]


async def filter(
    min_price: float = 0.0,
    min_avg_volume: int = 0,
    optionable: bool = False,
    limit: int | None = None,
) -> list[AssetRecord]:
    records = [r for r in await tradable_equities() if not optionable or r.optionable]
    if min_price <= 0 and min_avg_volume <= 0:
        return records[:limit] if limit else records

    checked = await asyncio.gather(
        *[_passes_market_filters(r.symbol, min_price, min_avg_volume) for r in records[:1500]],
        return_exceptions=True,
    )
    out = [
        record
        for record, ok in zip(records[:1500], checked)
        if ok is True
    ]
    return out[:limit] if limit else out


async def _fetch_alpaca_assets() -> list[dict[str, Any]]:
    settings = get_settings()
    settings.validate_alpaca_credentials()
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key.get_secret_value(),
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret.get_secret_value(),
    }
    params = {"status": "active", "asset_class": "us_equity"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(ALPACA_ASSETS_URL, params=params, timeout=20) as resp:
            if resp.status in {401, 403}:
                raise RuntimeError(
                    "Alpaca authentication failed while loading assets. Check local paper keys."
                )
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, list) else []


async def _fetch_text(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=20) as resp:
            resp.raise_for_status()
            return await resp.text()


async def _fetch_sec_tickers() -> dict[str, dict]:
    async with aiohttp.ClientSession(headers={"User-Agent": "tradebot local research"}) as session:
        async with session.get(SEC_TICKERS_URL, timeout=20) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return {str(v.get("ticker", "")).upper(): v for v in data.values()}


def _parse_nasdaq_listed(text: str) -> dict[str, dict]:
    out = {}
    lines = [line for line in text.splitlines() if "|" in line]
    if not lines:
        return out
    headers = lines[0].split("|")
    for line in lines[1:]:
        if line.startswith("File Creation"):
            continue
        row = dict(zip(headers, line.split("|")))
        symbol = row.get("Symbol", "").upper()
        test_issue = row.get("Test Issue", "N") == "Y"
        if symbol and not test_issue:
            out[symbol] = {
                "name": row.get("Security Name", ""),
                "exchange": "NASDAQ",
                "etf": row.get("ETF", "N") == "Y",
            }
    return out


def _parse_other_listed(text: str) -> dict[str, dict]:
    out = {}
    lines = [line for line in text.splitlines() if "|" in line]
    if not lines:
        return out
    headers = lines[0].split("|")
    for line in lines[1:]:
        if line.startswith("File Creation"):
            continue
        row = dict(zip(headers, line.split("|")))
        symbol = row.get("ACT Symbol", "").upper()
        test_issue = row.get("Test Issue", "N") == "Y"
        if symbol and not test_issue:
            out[symbol] = {
                "name": row.get("Security Name", ""),
                "exchange": row.get("Exchange", ""),
                "etf": row.get("ETF", "N") == "Y",
            }
    return out


async def _passes_market_filters(symbol: str, min_price: float, min_avg_volume: int) -> bool:
    try:
        import yfinance as yf

        def _load() -> tuple[float, int]:
            ticker = yf.Ticker(symbol)
            fast = ticker.fast_info
            price = float(fast.get("last_price") or fast.get("regular_market_previous_close") or 0)
            avg_volume = int(fast.get("ten_day_average_volume") or 0)
            return price, avg_volume

        price, avg_volume = await asyncio.to_thread(_load)
        return price >= min_price and avg_volume >= min_avg_volume
    except Exception:
        return False
