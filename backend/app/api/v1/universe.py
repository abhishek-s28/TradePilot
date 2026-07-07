"""Universe API — stock lists and symbol search across NYSE, NASDAQ, TSX, and more.

Endpoints:
  GET /universe/exchanges          – list supported exchanges
  GET /universe/list?exchange=NYSE – stocks for a given exchange/preset
  GET /universe/search?q=apple     – symbol + name search
  POST /universe/validate          – validate a list of symbols via live data
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/universe", tags=["universe"])


# ── Static curated universes ──────────────────────────────────────────────────
# Updated regularly; covers the most liquid, tradeable names per exchange.

_UNIVERSES: dict[str, dict[str, Any]] = {
    "SP500": {
        "label": "S&P 500 (Top 100)",
        "exchange": "NYSE/NASDAQ",
        "description": "Top 100 S&P 500 constituents by market cap",
        "symbols": [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B", "LLY", "AVGO",
            "JPM", "TSLA", "UNH", "V", "MA", "PG", "HD", "COST", "JNJ", "MRK",
            "ABBV", "BAC", "CRM", "KO", "CVX", "AMD", "WMT", "NFLX", "ORCL", "TMO",
            "ABT", "CSCO", "LIN", "PEP", "MCD", "IBM", "GE", "TXN", "PM", "ACN",
            "QCOM", "AMAT", "NEE", "DIS", "CAT", "GS", "INTC", "MS", "INTU", "BLK",
            "UPS", "HON", "ELV", "RTX", "LOW", "AMGN", "DE", "BKNG", "SPGI", "ISRG",
            "MDT", "PLD", "AXP", "SYK", "GILD", "ADI", "REGN", "SCHW", "VRTX", "ZTS",
            "MMC", "MO", "T", "ETN", "PGR", "ADP", "CI", "SO", "TJX", "DUK",
            "MDLZ", "WM", "BMY", "CL", "ICE", "SLB", "USB", "EW", "FDX", "NOC",
            "ITW", "GD", "PSA", "MCO", "MSCI", "NSC", "HCA", "F", "OXY", "COP",
        ],
    },
    "NASDAQ100": {
        "label": "NASDAQ 100",
        "exchange": "NASDAQ",
        "description": "Top NASDAQ 100 technology and growth leaders",
        "symbols": [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "COST",
            "NFLX", "AMD", "ADBE", "QCOM", "INTU", "CSCO", "AMAT", "TXN", "AMGN", "ISRG",
            "VRTX", "GILD", "ADI", "REGN", "LRCX", "KLAC", "MRVL", "SNPS", "CDNS", "MCHP",
            "PANW", "CRWD", "FTNT", "ZS", "DDOG", "TEAM", "WDAY", "OKTA", "SNOW", "PLTR",
            "ABNB", "MELI", "PYPL", "IDXX", "ANSS", "CTAS", "PCAR", "BIIB", "MRNA", "ILMN",
            "FAST", "ROST", "SIRI", "PAYX", "VRSK", "CPRT", "ODFL", "EXC", "XEL", "AEP",
            "FANG", "CEG", "PEP", "DLTR", "MNST", "SBUX", "BKNG", "LULU", "CME", "GEHC",
            "TTWO", "EA", "NXPI", "MXIM", "ON", "TER", "SWKS", "QRVO", "ENPH", "ALGN",
            "DXCM", "HOLX", "PODD", "RVMD", "SMCI", "ARM", "MSTR", "APP", "RBLX", "COIN",
            "HOOD", "IONQ", "NBIS", "SOUN", "WOLF", "BTDR", "RXRX", "KTOS", "RCAT", "ACHR",
        ],
    },
    "NYSE": {
        "label": "NYSE Blue Chips",
        "exchange": "NYSE",
        "description": "Top NYSE-listed blue chip stocks",
        "symbols": [
            "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "AXP", "USB", "TFC",
            "PNC", "COF", "SCHW", "ICE", "CME", "MCO", "SPGI", "FIS", "FI", "PYPL",
            "JNJ", "UNH", "CVS", "MCK", "ABC", "CAH", "HCA", "ELV", "CI", "HUM",
            "PG", "KO", "PEP", "MO", "PM", "KHC", "GIS", "K", "CPB", "SJM",
            "WMT", "TGT", "HD", "LOW", "COST", "DG", "DLTR", "KR", "SYY", "MCD",
            "XOM", "CVX", "COP", "SLB", "BKR", "OXY", "HAL", "PSX", "MPC", "VLO",
            "BA", "CAT", "GE", "HON", "RTX", "LMT", "GD", "NOC", "DE", "EMR",
            "UPS", "FDX", "CSX", "NSC", "UNP", "DAL", "UAL", "AAL", "LUV", "ALK",
            "VZ", "T", "TMUS", "CMCSA", "CHTR", "DIS", "FOX", "FOXA", "WBD", "PARA",
            "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "WEC", "ES",
        ],
    },
    "TSX": {
        "label": "TSX 60 (Canada)",
        "exchange": "TSX",
        "description": "S&P/TSX 60 — Canada's premier blue chip index",
        "symbols": [
            "TD.TO", "RY.TO", "ENB.TO", "CNQ.TO", "BCE.TO", "BNS.TO", "BMO.TO", "MFC.TO",
            "TRP.TO", "SU.TO", "ABX.TO", "ATD.TO", "BAM.TO", "CP.TO", "CNR.TO", "CVE.TO",
            "DOL.TO", "EMA.TO", "FTS.TO", "GIB-A.TO", "IFC.TO", "IMO.TO", "L.TO", "MG.TO",
            "NA.TO", "NTR.TO", "OTEX.TO", "POW.TO", "PPL.TO", "QBR-B.TO", "RCI-B.TO",
            "SHOP.TO", "SJR-B.TO", "STN.TO", "T.TO", "TIH.TO", "WCN.TO", "WPM.TO",
            "X.TO", "CCO.TO", "FM.TO", "K.TO", "AGI.TO", "AEM.TO", "KL.TO", "WDO.TO",
            "NFI.TO", "BIP-UN.TO", "BEP-UN.TO", "INE.TO", "NPI.TO", "AQN.TO", "H.TO",
            "CAE.TO", "CTC-A.TO", "GWO.TO", "SNC.TO", "IAG.TO", "FFH.TO", "EFN.TO",
        ],
    },
    "LSE": {
        "label": "LSE Top 50 (UK)",
        "exchange": "LSE",
        "description": "Top FTSE 100 stocks listed on the London Stock Exchange",
        "symbols": [
            "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "DGE.L", "BP.L", "RIO.L", "GSK.L",
            "BATS.L", "REL.L", "VOD.L", "NG.L", "BHP.L", "LLOY.L", "BARC.L", "AAL.L",
            "GLEN.L", "PRU.L", "LGEN.L", "STAN.L", "NWG.L", "MNDI.L", "LAND.L", "SGE.L",
            "CPG.L", "RDSA.L", "IMB.L", "WPP.L", "IHG.L", "EXPN.L", "CRH.L", "FRES.L",
            "ANTO.L", "EVR.L", "FORM.L", "PSN.L", "BLND.L", "EMG.L", "BRBY.L", "JD.L",
            "SMWH.L", "AUTO.L", "ADM.L", "HLMA.L", "RS1.L", "OCDO.L", "AHT.L", "SBRY.L",
            "MKS.L", "TSCO.L",
        ],
    },
    "ETF": {
        "label": "Major ETFs",
        "exchange": "NYSE/NASDAQ",
        "description": "Most liquid US-listed ETFs covering equity, bonds, sectors, and commodities",
        "symbols": [
            # Broad market
            "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "VEA", "VWO", "EFA",
            # Sectors
            "XLF", "XLK", "XLE", "XLV", "XLI", "XLB", "XLC", "XLY", "XLP", "XLRE", "XLU",
            # Thematic
            "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "BOTZ", "AIQ", "ROBO", "WCLD",
            # Bond
            "TLT", "IEF", "SHY", "BND", "AGG", "HYG", "LQD", "TIP", "BNDX",
            # Commodity
            "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG", "PDBC", "DJP",
            # Leveraged (for research)
            "TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "UVXY", "VXX",
            # International
            "EWJ", "EWZ", "EWG", "EWU", "EWC", "EWY", "EWA", "FXI", "KWEB", "MCHI",
        ],
    },
    "GROWTH": {
        "label": "High-Growth Tech",
        "exchange": "NYSE/NASDAQ",
        "description": "High-growth technology, AI, and innovation companies",
        "symbols": [
            "NVDA", "AMD", "SMCI", "ARM", "PLTR", "COIN", "MSTR", "HOOD", "RBLX",
            "SNOW", "DDOG", "CRWD", "PANW", "ZS", "OKTA", "TEAM", "WDAY", "NOW",
            "ADBE", "CRM", "VEEV", "HUBS", "SHOP", "SQ", "SOFI", "AFRM", "UPST",
            "APP", "TTD", "PUBM", "MGNI", "DV", "IAS", "BURL", "CELH", "MNDY",
            "GTLB", "BILL", "PCTY", "PAYC", "DOCU", "ZM", "TWLO", "SMAR", "BOX",
            "DBX", "FIVN", "NICE", "SNCR", "AI", "BBAI", "GFAI", "SOUN", "IONQ",
            "QBTS", "RGTI", "QUBT", "ARQQ", "ACMR", "AEHR", "Wolf", "AEVA", "MVIS",
        ],
    },
    "CRYPTO_RELATED": {
        "label": "Crypto-Adjacent Equities",
        "exchange": "NYSE/NASDAQ",
        "description": "Publicly traded companies with significant crypto/blockchain exposure",
        "symbols": [
            "COIN", "MSTR", "HOOD", "MARA", "RIOT", "CLSK", "BTBT", "HUT", "BITF",
            "CIFR", "WULF", "IREN", "CORZ", "BTDR", "ARBK", "BRPHF", "SOS", "BFGN",
        ],
    },
    "WATCHLIST": {
        "label": "Default Watchlist",
        "exchange": "NYSE/NASDAQ",
        "description": "Starting watchlist — mix of tech, financials, and macro ETFs",
        "symbols": [
            "AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL", "AMZN", "AMD",
            "JPM", "BAC", "GS", "V", "MA",
            "SPY", "QQQ", "IWM", "TLT", "GLD",
        ],
    },
}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/exchanges")
async def list_exchanges() -> list[dict]:
    """List all supported exchanges/universe presets."""
    return [
        {
            "id": k,
            "label": v["label"],
            "exchange": v["exchange"],
            "description": v["description"],
            "count": len(v["symbols"]),
        }
        for k, v in _UNIVERSES.items()
    ]


@router.get("/list")
async def list_symbols(exchange: str = Query(default="WATCHLIST")) -> dict:
    """Return the symbol list for a given exchange preset."""
    key = exchange.upper()
    if key not in _UNIVERSES:
        raise HTTPException(
            400,
            f"Unknown exchange/preset '{exchange}'. "
            f"Valid values: {', '.join(_UNIVERSES.keys())}",
        )
    u = _UNIVERSES[key]
    return {
        "exchange": key,
        "label": u["label"],
        "description": u["description"],
        "count": len(u["symbols"]),
        "symbols": u["symbols"],
    }


@router.get("/search")
async def search_symbols(q: str = Query(min_length=1)) -> dict:
    """Search for a stock symbol or company name.

    First scans the local static universe, then validates against Yahoo Finance.
    Returns matches with basic metadata.
    """
    q_upper = q.upper().strip()
    q_lower = q.lower().strip()

    # 1) Fast static search across all curated lists
    seen: set[str] = set()
    matches: list[dict] = []
    for preset in _UNIVERSES.values():
        for sym in preset["symbols"]:
            if sym in seen:
                continue
            if q_upper in sym.upper() or q_lower in sym.lower():
                seen.add(sym)
                matches.append({"symbol": sym, "source": "curated"})

    # 2) If the query looks like an exact ticker (1-6 chars, alpha+dot), try live validation
    if len(q) <= 6 and q.replace(".", "").replace("-", "").isalpha():
        try:
            info = await _yf_info(q_upper)
            if info and info.get("regularMarketPrice"):
                sym_exists = q_upper not in seen
                if sym_exists:
                    matches.insert(
                        0,
                        {
                            "symbol": q_upper,
                            "name": info.get("longName") or info.get("shortName", ""),
                            "exchange": info.get("exchange", ""),
                            "sector": info.get("sector", ""),
                            "source": "live",
                        },
                    )
        except Exception as exc:
            log.debug("universe.search_live_failed", q=q, error=str(exc))

    return {"query": q, "count": len(matches), "results": matches[:50]}


class ValidateRequest(BaseModel):
    symbols: list[str]


@router.post("/validate")
async def validate_symbols(req: ValidateRequest) -> dict:
    """Validate a list of symbols against Yahoo Finance — confirms they trade."""
    results: dict[str, dict] = {}
    tasks = [(sym, _yf_info(sym.upper())) for sym in req.symbols[:50]]

    async def _check(sym: str, coro: Any) -> tuple[str, dict]:
        try:
            info = await coro
            valid = bool(info and info.get("regularMarketPrice"))
            return sym, {
                "valid": valid,
                "name": (info or {}).get("longName") or (info or {}).get("shortName", ""),
                "exchange": (info or {}).get("exchange", ""),
            }
        except Exception:
            return sym, {"valid": False, "name": "", "exchange": ""}

    pairs = await asyncio.gather(*[_check(s, c) for s, c in tasks])
    return {"results": dict(pairs)}


# ── internal helper ───────────────────────────────────────────────────────────

async def _yf_info(symbol: str) -> dict | None:
    """Fetch basic Yahoo Finance info for a symbol (thread-offloaded)."""
    import yfinance as yf

    def _fetch() -> dict:
        return yf.Ticker(symbol).info

    try:
        return await asyncio.to_thread(_fetch)
    except Exception:
        return None
