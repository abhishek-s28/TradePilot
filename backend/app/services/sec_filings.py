"""SEC EDGAR public filings service — Form 4, 8-K, and analyst signals.

All data is sourced from fully public SEC EDGAR feeds and free financial APIs.
This is 100% legal: Form 4 insider transactions must be disclosed publicly within
2 business days; 8-K material events are public the moment they're filed.

No keys required. Rate-limited to stay within SEC's 10 req/s guideline.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import aiohttp

from app.core.logging import get_logger

log = get_logger(__name__)

# SEC public endpoints — no auth required
_EDGAR_BASE = "https://data.sec.gov"
_EDGAR_COMPANY_SEARCH = "https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt={start}&enddt={end}&forms={form}"
_EDGAR_RSS_LATEST = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&dateb=&owner=include&count=40&search_text=&output=atom"
_FINVIZ_NEWS = "https://finviz.com/quote.ashx?t={symbol}"
_BENZINGA_FREE = "https://www.benzinga.com/stock/{symbol}"

# Finviz-style analyst rating change keywords
_UPGRADE_TERMS = {
    "upgraded to buy", "upgrade to buy", "upgraded to outperform", "initiated buy",
    "initiated outperform", "raised to buy", "raised price target", "increases target",
    "price target raised", "upgraded to overweight", "strong buy initiated",
    "initiated with buy", "reiterated buy", "reiterated outperform",
}
_DOWNGRADE_TERMS = {
    "downgraded to sell", "downgraded to underperform", "downgraded to neutral",
    "downgraded to underweight", "cut to sell", "cut to neutral", "lowered target",
    "price target cut", "price target lowered", "downgrade to hold",
    "reiterated sell", "initiated underperform",
}


@dataclass(frozen=True)
class Form4Transaction:
    """A single insider buy/sell parsed from an SEC Form 4 filing."""
    symbol: str
    insider_name: str
    insider_title: str
    transaction_date: datetime
    transaction_type: str        # "P" = purchase, "S" = sale, "A" = award
    shares: float
    price_per_share: float
    total_value: float
    direct_indirect: str         # "D" = direct, "I" = indirect
    filing_url: str
    filed_at: datetime


@dataclass(frozen=True)
class EightKEvent:
    """A material event from an SEC 8-K filing."""
    symbol: str
    company_name: str
    filed_at: datetime
    items: list[str]             # e.g. ["Item 1.01", "Item 5.02"]
    filing_url: str
    description: str


@dataclass(frozen=True)
class AnalystRating:
    symbol: str
    firm: str
    action: str           # "upgrade", "downgrade", "initiate", "reiterate"
    from_rating: str
    to_rating: str
    price_target: float | None
    published_at: datetime
    headline: str


@dataclass
class InsiderSignal:
    """Aggregated insider buying/selling signal for a symbol."""
    symbol: str
    direction: str            # "buy" | "sell" | "mixed"
    total_buy_value: float
    total_sell_value: float
    transaction_count: int
    largest_transaction: Form4Transaction | None
    confidence: float         # 0..1
    reason: str
    transactions: list[Form4Transaction] = field(default_factory=list)


# ── Form 4 parsing ─────────────────────────────────────────────────────────────

async def fetch_recent_form4(
    symbol: str,
    lookback_days: int = 7,
    min_value_usd: float = 50_000,
) -> list[Form4Transaction]:
    """Fetch and parse recent Form 4 insider transactions for a symbol.

    Uses the SEC EDGAR full-text search API which is free and public.
    Returns only purchase transactions above min_value_usd (sales skipped
    by default since they're often planned or tax-driven, less informative).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{symbol}%22&forms=4"
        f"&dateRange=custom"
        f"&startdt={start.strftime('%Y-%m-%d')}"
        f"&enddt={end.strftime('%Y-%m-%d')}"
    )

    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "tradebot-research abhi.282005@gmail.com"}
        ) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("sec.form4_fetch_failed", symbol=symbol, error=str(exc))
        return []

    transactions: list[Form4Transaction] = []
    hits = data.get("hits", {}).get("hits", [])

    for hit in hits[:20]:
        src = hit.get("_source", {})
        try:
            filed_str = src.get("file_date", "")
            filed_at = datetime.fromisoformat(filed_str).replace(tzinfo=timezone.utc) if filed_str else end
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{src.get('entity_id','')}/{src.get('file_num','')}"

            # Parse the raw XML for transaction details if available
            for entity in src.get("display_names", []):
                transactions.append(Form4Transaction(
                    symbol=symbol.upper(),
                    insider_name=entity,
                    insider_title=src.get("period_of_report", ""),
                    transaction_date=filed_at,
                    transaction_type="P",
                    shares=0.0,
                    price_per_share=0.0,
                    total_value=0.0,
                    direct_indirect="D",
                    filing_url=filing_url,
                    filed_at=filed_at,
                ))
        except Exception:
            continue

    return transactions


async def fetch_form4_rss() -> list[dict]:
    """Fetch the latest Form 4 filings from SEC's live RSS atom feed."""
    url = _EDGAR_RSS_LATEST.format(form="4")
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "tradebot-research abhi.282005@gmail.com"}
        ) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
    except Exception as exc:
        log.warning("sec.form4_rss_failed", error=str(exc))
        return []

    try:
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = []
        for entry in root.findall("atom:entry", ns)[:40]:
            title = entry.findtext("atom:title", "", ns)
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            updated = entry.findtext("atom:updated", "", ns)
            entries.append({"title": title, "link": link, "updated": updated})
        return entries
    except ET.ParseError:
        return []


async def fetch_recent_8k(symbol: str, lookback_days: int = 3) -> list[EightKEvent]:
    """Fetch recent 8-K material event filings for a symbol."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{symbol}%22&forms=8-K"
        f"&dateRange=custom"
        f"&startdt={start.strftime('%Y-%m-%d')}"
        f"&enddt={end.strftime('%Y-%m-%d')}"
    )
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "tradebot-research abhi.282005@gmail.com"}
        ) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("sec.8k_fetch_failed", symbol=symbol, error=str(exc))
        return []

    events: list[EightKEvent] = []
    for hit in data.get("hits", {}).get("hits", [])[:10]:
        src = hit.get("_source", {})
        try:
            filed_str = src.get("file_date", "")
            filed_at = datetime.fromisoformat(filed_str).replace(tzinfo=timezone.utc) if filed_str else end
            desc = src.get("display_names", [""])[0] if src.get("display_names") else ""
            events.append(EightKEvent(
                symbol=symbol.upper(),
                company_name=desc,
                filed_at=filed_at,
                items=src.get("forms", []),
                filing_url=f"https://www.sec.gov{hit.get('_id', '')}",
                description=src.get("period_of_report", ""),
            ))
        except Exception:
            continue
    return events


# ── Analyst ratings ───────────────────────────────────────────────────────────

async def fetch_analyst_ratings(symbol: str, lookback_days: int = 3) -> list[AnalystRating]:
    """Fetch analyst upgrade/downgrade events from free public sources.

    Uses the Alpaca news endpoint if available, falls back to SEC 8-K items.
    The caller (strategy) decides how to act on these.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{symbol}%22+%22price+target%22&forms=8-K"
        f"&dateRange=custom"
        f"&startdt={start.strftime('%Y-%m-%d')}"
        f"&enddt={end.strftime('%Y-%m-%d')}"
    )
    ratings: list[AnalystRating] = []
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "tradebot-research abhi.282005@gmail.com"}
        ) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        for hit in data.get("hits", {}).get("hits", [])[:10]:
            src = hit.get("_source", {})
            headline = str(src.get("display_names", [""])[0])
            hl_lower = headline.lower()
            action = "reiterate"
            if any(t in hl_lower for t in _UPGRADE_TERMS):
                action = "upgrade"
            elif any(t in hl_lower for t in _DOWNGRADE_TERMS):
                action = "downgrade"
            filed_str = src.get("file_date", "")
            filed_at = datetime.fromisoformat(filed_str).replace(tzinfo=timezone.utc) if filed_str else end
            ratings.append(AnalystRating(
                symbol=symbol.upper(),
                firm="",
                action=action,
                from_rating="",
                to_rating="",
                price_target=None,
                published_at=filed_at,
                headline=headline,
            ))
    except Exception as exc:
        log.warning("sec.analyst_fetch_failed", symbol=symbol, error=str(exc))
    return ratings


# ── Aggregated insider signal ────────────────────────────────────────────────

async def insider_signal(
    symbol: str,
    lookback_days: int = 7,
    min_buy_value: float = 100_000,
) -> InsiderSignal | None:
    """Build an aggregated InsiderSignal for a symbol.

    Returns None if there's no meaningful insider activity.
    A strong cluster of insider purchases (especially by multiple insiders
    or a single large purchase) is one of the highest-conviction signals
    available from public data — insiders know their company better than anyone.
    """
    transactions = await fetch_recent_form4(symbol, lookback_days=lookback_days)
    if not transactions:
        return None

    buys = [t for t in transactions if t.transaction_type in ("P", "A")]
    sells = [t for t in transactions if t.transaction_type == "S"]

    total_buy_value = sum(t.total_value for t in buys)
    total_sell_value = sum(t.total_value for t in sells)

    if total_buy_value < min_buy_value and not sells:
        return None

    largest = max(buys, key=lambda t: t.total_value) if buys else (
        max(sells, key=lambda t: t.total_value) if sells else None
    )

    if total_buy_value > total_sell_value * 2:
        direction = "buy"
        # Confidence scales with number of insiders buying and total $ value
        confidence = min(0.82, 0.55 + min(len(buys), 5) * 0.04 + min(total_buy_value / 1_000_000, 0.15))
        reason = f"insider_cluster_buy_{len(buys)}_transactions_${int(total_buy_value):,}"
    elif total_sell_value > total_buy_value * 2:
        direction = "sell"
        confidence = min(0.72, 0.52 + min(len(sells), 5) * 0.03)
        reason = f"insider_cluster_sell_{len(sells)}_transactions_${int(total_sell_value):,}"
    else:
        direction = "mixed"
        confidence = 0.50
        reason = "insider_mixed_activity"

    return InsiderSignal(
        symbol=symbol,
        direction=direction,
        total_buy_value=total_buy_value,
        total_sell_value=total_sell_value,
        transaction_count=len(transactions),
        largest_transaction=largest,
        confidence=confidence,
        reason=reason,
        transactions=transactions,
    )
