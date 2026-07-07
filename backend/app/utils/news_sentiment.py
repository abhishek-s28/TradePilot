"""Keyword-based sentiment scoring for news headlines.

No external NLP dependency — fast, explainable heuristic scorer for gating
and weighting trade signals. Covers five signal categories:

  polarity: -1 (very bearish) .. +1 (very bullish), weighted keyword hits
  impact:   1.0 (routine) .. ~3.0 (market-moving), from high-impact actors/events

Categories tracked:
  earnings / guidance / revenue
  M&A / corporate events
  regulatory / legal / government
  macro / Fed / rates / geopolitical
  analyst ratings (upgrades, downgrades, price targets)
  insider activity (Form 4 cluster buys/sells)
  sector-specific: pharma FDA, energy, crypto, tech
  sentiment / momentum language
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.domain import NewsItem

# ── Positive (bullish) keywords ───────────────────────────────────────────────
_POSITIVE: dict[str, float] = {
    # Earnings beats
    "beats": 1.0, "beat estimates": 1.3, "tops estimates": 1.2, "blowout earnings": 1.5,
    "earnings beat": 1.3, "eps beat": 1.2, "revenue beat": 1.2,
    "better than expected": 1.1, "exceeds expectations": 1.2,
    "record earnings": 1.3, "record revenue": 1.3, "record profit": 1.3,
    "record quarter": 1.2, "record annual": 1.2,
    # Guidance / forecast
    "raises guidance": 1.4, "raised guidance": 1.4, "raises forecast": 1.3,
    "increased guidance": 1.3, "lifted outlook": 1.3, "improves outlook": 1.2,
    "upward revision": 1.2, "positive outlook": 1.0,
    # Price / momentum
    "surge": 1.2, "surges": 1.2, "soars": 1.3, "soar": 1.2, "rally": 0.9,
    "rallies": 0.9, "jumps": 1.0, "jump": 0.9, "spikes": 1.1, "breakout": 1.0,
    "all-time high": 1.3, "52-week high": 1.2, "new high": 1.1, "gains": 0.7,
    "powers higher": 1.0, "climbs": 0.8, "advances": 0.7,
    # Analyst upgrades
    "upgraded to buy": 1.3, "upgrade to buy": 1.3, "upgraded to outperform": 1.2,
    "upgraded to overweight": 1.2, "initiated buy": 1.2, "initiated outperform": 1.2,
    "strong buy initiated": 1.3, "price target raised": 1.2, "raises price target": 1.2,
    "increased price target": 1.2, "outperform": 0.9, "overweight": 0.9,
    "reiterated buy": 1.0, "reiterated outperform": 1.0,
    # Corporate actions
    "buyback": 1.0, "share buyback": 1.1, "stock repurchase": 1.1,
    "dividend increase": 1.1, "dividend hike": 1.1, "special dividend": 1.0,
    "stock split": 1.1, "reverse split canceled": 0.8,
    "acquires": 0.9, "acquisition": 0.8, "merger approved": 1.2, "deal closed": 1.0,
    "partnership": 0.7, "strategic alliance": 0.8, "joint venture": 0.7,
    "wins contract": 1.1, "awarded contract": 1.1, "government contract": 1.0,
    "expands": 0.7, "expansion": 0.7, "market share gains": 0.9,
    # Regulatory / legal wins
    "fda approval": 1.5, "fda approved": 1.5, "fda clearance": 1.4,
    "approved by fda": 1.5, "breakthrough therapy": 1.3, "fast track designation": 1.2,
    "patent granted": 1.0, "wins lawsuit": 1.1, "acquitted": 0.9,
    "cleared of charges": 0.9, "antitrust cleared": 1.0, "merger cleared": 1.1,
    "regulatory approval": 1.2,
    # Macro / rates (bullish)
    "rate cut": 1.2, "cuts rates": 1.2, "rate pause": 0.8, "dovish": 1.1,
    "stimulus": 1.0, "quantitative easing": 1.0, "fiscal stimulus": 1.0,
    "tax cut": 0.9, "jobs report beats": 1.1, "strong jobs": 1.0,
    "inflation cools": 1.1, "cpi falls": 1.1, "deflation": 0.8,
    "soft landing": 1.0, "gdp beats": 1.0, "economy grows": 0.9,
    # Insider activity (bullish)
    "insider buying": 1.3, "insider purchase": 1.3, "ceo buys": 1.4,
    "director buys": 1.2, "officers buy": 1.2, "cluster buy": 1.4,
    "form 4 purchase": 1.3, "insider cluster": 1.3,
    # Sector: energy / crypto
    "opec cuts production": 1.1, "oil supply cut": 1.1,
    "bitcoin etf approved": 1.4, "crypto rally": 0.9,
    # Momentum / analyst language
    "bullish": 0.9, "strong demand": 1.0, "robust demand": 1.0, "solid results": 0.9,
    "strong growth": 1.0, "accelerating growth": 1.1, "profit jumps": 1.2,
    "cash flow positive": 0.9, "margin expansion": 1.0, "cost cuts": 0.7,
    "operational improvement": 0.8,
}

# ── Negative (bearish) keywords ───────────────────────────────────────────────
_NEGATIVE: dict[str, float] = {
    # Earnings misses
    "misses": 1.0, "miss estimates": 1.3, "earnings miss": 1.3, "eps miss": 1.2,
    "falls short": 1.1, "below expectations": 1.1, "disappoints": 1.0,
    "quarterly loss": 1.2, "net loss widens": 1.2, "profit warning": 1.3,
    # Guidance cuts
    "cuts guidance": 1.4, "cut guidance": 1.4, "lowers forecast": 1.3,
    "reduces outlook": 1.3, "warns of slowdown": 1.2, "lowered guidance": 1.4,
    "negative guidance": 1.3, "guidance cut": 1.4, "downward revision": 1.2,
    # Price / momentum
    "plunge": 1.3, "plunges": 1.3, "tumbles": 1.2, "slumps": 1.1, "sinks": 1.1,
    "crash": 1.4, "crashes": 1.4, "selloff": 1.1, "sell-off": 1.1, "plummets": 1.3,
    "free fall": 1.3, "collapses": 1.3, "drops sharply": 1.1, "falls": 0.7,
    "new low": 1.1, "52-week low": 1.2, "all-time low": 1.3,
    # Analyst downgrades
    "downgraded to sell": 1.3, "downgrade to sell": 1.3, "downgraded to neutral": 1.1,
    "cut to sell": 1.3, "cut to neutral": 1.1, "price target cut": 1.2,
    "price target lowered": 1.2, "lowers price target": 1.2, "underperform": 1.0,
    "underweight": 1.0, "reiterated sell": 1.1, "downgraded to underweight": 1.2,
    # Legal / regulatory
    "lawsuit": 1.1, "sued": 1.0, "class action": 1.2, "securities fraud": 1.4,
    "investigation": 1.1, "probe": 1.0, "sec charges": 1.4, "doj charges": 1.4,
    "fraud charges": 1.4, "criminal charges": 1.4, "indicted": 1.4,
    "consent decree": 1.1, "fda rejection": 1.4, "fda refuse": 1.4,
    "clinical trial failed": 1.4, "trial failure": 1.4, "drug fails": 1.3,
    "recall": 1.2, "product recall": 1.3, "safety warning": 1.1,
    "ban": 1.1, "banned": 1.1, "blocked": 0.9, "antitrust blocked": 1.2,
    "merger blocked": 1.2, "deal rejected": 1.1,
    # Corporate distress
    "bankruptcy": 1.6, "files for bankruptcy": 1.7, "chapter 11": 1.6,
    "chapter 7": 1.6, "defaults": 1.4, "debt default": 1.4, "bond default": 1.4,
    "debt restructuring": 1.2, "going concern": 1.4, "insolvency": 1.4,
    "delisted": 1.4, "nasdaq delisting": 1.4, "nyse delisting": 1.4,
    "trading halted": 1.4, "halts": 1.1, "suspended": 1.0,
    "ceo fired": 1.2, "ceo ousted": 1.2, "ceo resigns abruptly": 1.3,
    "resigns": 0.9, "resignation": 0.9, "steps down": 0.9,
    "layoffs": 1.1, "job cuts": 1.1, "mass layoffs": 1.3, "restructuring": 1.0,
    "workforce reduction": 1.1, "headcount cut": 1.0,
    # Macro / rates (bearish)
    "rate hike": 1.1, "hikes rates": 1.1, "raises rates": 1.1, "hawkish": 1.1,
    "inflation surges": 1.2, "inflation accelerates": 1.1, "cpi rises": 1.0,
    "recession": 1.2, "contraction": 1.0, "gdp falls": 1.0, "slowdown": 0.9,
    "stagflation": 1.2, "yield curve inverts": 1.2, "credit crunch": 1.3,
    "tariff": 1.2, "tariffs": 1.2, "trade war escalates": 1.4,
    "sanctions": 1.2, "export ban": 1.2, "trade restrictions": 1.1,
    "supply chain disruption": 1.0, "shortage": 0.9,
    # Cyber / operational
    "data breach": 1.2, "cyberattack": 1.2, "ransomware": 1.2, "hack": 1.1,
    "outage": 0.8, "service disruption": 0.7, "system failure": 0.8,
    # Insider selling (unusual)
    "insider selling": 1.1, "insider dump": 1.3, "ceo sells all": 1.4,
    "director sells": 0.8, "cluster sell": 1.2,
    # Miscellaneous
    "bearish": 0.9, "warns": 0.9, "warning": 0.9, "misconduct": 1.1,
    "scandal": 1.2, "whistle-blower": 1.1, "short seller": 1.0,
    "short report": 1.2, "fraud allegations": 1.3,
}

# ── High-impact multiplier terms ───────────────────────────────────────────────
# These amplify whatever directional polarity the headline already has.
# They don't carry direction themselves — "Fed" can be bullish or bearish
# depending on the rest of the headline.
_HIGH_IMPACT: dict[str, float] = {
    # US executive / administration
    "trump": 1.7, "white house": 1.5, "executive order": 1.6,
    "president": 1.3, "administration": 1.2, "congress passes": 1.3,
    "senate approves": 1.2, "house passes": 1.2,
    # Fed / monetary policy
    "federal reserve": 1.6, "fed chair": 1.6, "jerome powell": 1.6,
    "fomc": 1.6, "fomc meeting": 1.6, "fomc minutes": 1.5,
    "interest rate decision": 1.6, "rate decision": 1.5, "fed pivot": 1.6,
    "quantitative tightening": 1.4, "balance sheet reduction": 1.3,
    # Trade / geopolitical
    "tariff": 1.5, "tariffs": 1.5, "trade war": 1.5, "trade deal": 1.4,
    "china tariffs": 1.6, "trade truce": 1.4,
    "war": 1.4, "military conflict": 1.4, "sanctions": 1.4,
    "geopolitical": 1.3, "opec": 1.3, "oil embargo": 1.4,
    # Regulatory / enforcement
    "sec investigation": 1.5, "sec charges": 1.6, "doj": 1.4,
    "antitrust": 1.4, "monopoly ruling": 1.4, "consent order": 1.3,
    "fda": 1.4, "fda decision": 1.5, "clinical hold": 1.5,
    # CEO / leadership
    "elon musk": 1.4, "musk": 1.4, "jensen huang": 1.3, "sam altman": 1.3,
    "ceo steps down": 1.4, "ceo fired": 1.4, "ceo arrested": 1.6,
    # Corporate events
    "earnings call": 1.2, "guidance": 1.2, "merger": 1.2, "acquisition": 1.2,
    "takeover bid": 1.4, "hostile takeover": 1.5, "going private": 1.4,
    "ipo": 1.2, "spin-off": 1.2, "bankruptcy filing": 1.7,
    "stock split": 1.2, "halted trading": 1.5, "trading halt": 1.5,
    # Macro data releases
    "jobs report": 1.3, "nonfarm payrolls": 1.3, "cpi report": 1.3,
    "pce data": 1.3, "gdp report": 1.2, "retail sales": 1.1,
    # Analyst cluster
    "multiple analysts": 1.2, "wall street consensus": 1.2,
    "analyst day": 1.2, "investor day": 1.2,
    # Insider / public filing
    "form 4": 1.2, "13d filing": 1.3, "schedule 13d": 1.3, "activist investor": 1.3,
    "warren buffett": 1.5, "berkshire": 1.4, "carl icahn": 1.4, "bill ackman": 1.4,
    "short seller report": 1.4, "hindenburg": 1.5, "citron": 1.4,
}


@dataclass(frozen=True)
class SentimentScore:
    polarity: float       # -1..+1, weighted-average across matched headlines
    impact: float         # 1.0..~3.0, multiplier from high-impact terms
    magnitude: float      # |polarity| * impact — overall "how hard does this hit"
    headline_count: int
    top_headline: str = ""

    @property
    def direction(self) -> str:
        if self.polarity > 0.08:
            return "bullish"
        if self.polarity < -0.08:
            return "bearish"
        return "neutral"


def score_text(text: str) -> tuple[float, float]:
    """Score a single piece of text. Returns (polarity, impact)."""
    lowered = text.lower()
    pos = sum(weight for kw, weight in _POSITIVE.items() if kw in lowered)
    neg = sum(weight for kw, weight in _NEGATIVE.items() if kw in lowered)
    total = pos + neg
    polarity = 0.0 if total == 0 else (pos - neg) / total

    impact = 1.0
    for kw, weight in _HIGH_IMPACT.items():
        if kw in lowered:
            impact = max(impact, weight)
    return polarity, impact


def score_news(items: list[NewsItem], *, symbol: str | None = None) -> SentimentScore:
    """Aggregate sentiment across a batch of headlines.

    When `symbol` is given, headlines that explicitly tag it are weighted
    more heavily than broad market/macro headlines that merely mention it
    in passing via the symbols list.
    """
    if not items:
        return SentimentScore(polarity=0.0, impact=1.0, magnitude=0.0, headline_count=0)

    weighted_polarity = 0.0
    weight_sum = 0.0
    best_impact = 1.0
    top_headline = ""
    top_score = -1.0

    for item in items:
        text = f"{item.headline} {item.summary}"
        polarity, impact = score_text(text)
        if polarity == 0.0 and impact == 1.0:
            continue

        relevance = 1.0
        if symbol and item.symbols and symbol.upper() in {s.upper() for s in item.symbols}:
            relevance = 1.3

        w = impact * relevance
        weighted_polarity += polarity * w
        weight_sum += w
        best_impact = max(best_impact, impact)

        headline_score = abs(polarity) * impact
        if headline_score > top_score:
            top_score = headline_score
            top_headline = item.headline

    if weight_sum == 0.0:
        return SentimentScore(
            polarity=0.0, impact=1.0, magnitude=0.0,
            headline_count=len(items), top_headline=items[0].headline,
        )

    polarity = max(-1.0, min(1.0, weighted_polarity / weight_sum))
    magnitude = abs(polarity) * best_impact
    return SentimentScore(
        polarity=round(polarity, 3),
        impact=round(best_impact, 2),
        magnitude=round(magnitude, 3),
        headline_count=len(items),
        top_headline=top_headline,
    )
