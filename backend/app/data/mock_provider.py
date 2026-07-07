"""Mock market data — deterministic, seeded, no external calls.

Used for development, testing, and Research Mode when no API key is present.
Generates realistic-looking random-walk bars and synthetic options chains.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone

from app.data.base import MarketDataProvider
from app.models.domain import Bar, OptionContract, OptionRight, Quote


def _seed_for(symbol: str) -> int:
    h = hashlib.sha256(symbol.encode()).hexdigest()
    return int(h[:8], 16)


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    def __init__(self) -> None:
        self._connected = False
        self._base_prices: dict[str, float] = {}

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_market_open(self) -> bool:
        # Pretend market is open M-F 9:30am–4:00pm ET. Simplified: weekday in UTC.
        now = datetime.now(timezone.utc)
        return now.weekday() < 5

    def _price_for(self, symbol: str, at: datetime | None = None) -> float:
        if symbol not in self._base_prices:
            rng = random.Random(_seed_for(symbol))
            self._base_prices[symbol] = round(rng.uniform(20, 400), 2)
        base = self._base_prices[symbol]
        # Add a tiny intraday wobble
        t = (at or datetime.now(timezone.utc)).timestamp()
        wobble = math.sin(t / 600) * 0.5 + math.sin(t / 60) * 0.15
        return round(base + wobble, 2)

    async def get_quote(self, symbol: str) -> Quote:
        if not self._connected:
            raise RuntimeError("provider not connected")
        mid = self._price_for(symbol)
        spread = round(mid * 0.0005, 2) or 0.01
        return Quote(
            symbol=symbol,
            bid=round(mid - spread / 2, 2),
            ask=round(mid + spread / 2, 2),
            last=mid,
            bid_size=100,
            ask_size=100,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: await self.get_quote(s) for s in symbols}

    async def get_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]:
        rng = random.Random(_seed_for(symbol + timeframe))
        step = {"1Min": 60, "5Min": 300, "15Min": 900, "1H": 3600, "1Day": 86400}.get(
            timeframe, 60
        )
        bars: list[Bar] = []
        t = int(start.timestamp())
        end_t = int(end.timestamp())
        price = self._base_prices.get(symbol) or self._price_for(symbol)
        while t < end_t:
            drift = rng.gauss(0, price * 0.002)
            o = round(price, 2)
            c = round(price + drift, 2)
            h = round(max(o, c) + abs(rng.gauss(0, price * 0.001)), 2)
            l = round(min(o, c) - abs(rng.gauss(0, price * 0.001)), 2)
            bars.append(
                Bar(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(t, tz=timezone.utc),
                    open=o, high=h, low=l, close=c,
                    volume=rng.randint(10_000, 1_000_000),
                    vwap=round((h + l + c) / 3, 2),
                )
            )
            price = c
            t += step
        return bars

    async def get_options_chain(
        self, underlying: str, expiration: datetime | None = None
    ) -> list[OptionContract]:
        spot = self._price_for(underlying)
        rng = random.Random(_seed_for(underlying + "chain"))
        if expiration is None:
            # Next 3 Fridays
            base_date = datetime.now(timezone.utc) + timedelta(days=7)
            expirations = [base_date + timedelta(days=7 * i) for i in range(3)]
        else:
            expirations = [expiration]

        chain: list[OptionContract] = []
        for exp in expirations:
            strikes = [round(spot * (1 + d / 100), 1) for d in range(-15, 16, 5)]
            for strike in strikes:
                for right in (OptionRight.CALL, OptionRight.PUT):
                    intrinsic = (
                        max(0.0, spot - strike) if right == OptionRight.CALL
                        else max(0.0, strike - spot)
                    )
                    days_to_exp = max(1, (exp - datetime.now(timezone.utc)).days)
                    time_value = round(rng.uniform(0.3, 2.5) * math.sqrt(days_to_exp / 30), 2)
                    mid = round(intrinsic + time_value, 2)
                    spread = round(max(0.05, mid * 0.05), 2)
                    delta = self._mock_delta(spot, strike, right)
                    occ = self._occ_symbol(underlying, exp, right, strike)
                    chain.append(
                        OptionContract(
                            symbol=occ,
                            underlying=underlying,
                            expiration=exp,
                            strike=strike,
                            right=right,
                            bid=max(0.01, round(mid - spread / 2, 2)),
                            ask=round(mid + spread / 2, 2),
                            last=mid,
                            volume=rng.randint(0, 5000),
                            open_interest=rng.randint(0, 10000),
                            implied_volatility=round(rng.uniform(0.15, 0.6), 3),
                            delta=delta,
                            gamma=round(rng.uniform(0.001, 0.05), 4),
                            theta=round(-rng.uniform(0.01, 0.1), 4),
                            vega=round(rng.uniform(0.05, 0.3), 4),
                        )
                    )
        return chain

    async def get_option_quote(self, occ_symbol: str) -> OptionContract:
        # In a real provider this would fetch the specific contract.
        # For mock, parse the OCC and synthesize.
        underlying, exp, right, strike = self._parse_occ(occ_symbol)
        chain = await self.get_options_chain(underlying, exp)
        for c in chain:
            if c.symbol == occ_symbol:
                return c
        # Fallback synthesized
        return OptionContract(
            symbol=occ_symbol, underlying=underlying, expiration=exp,
            strike=strike, right=right,
        )

    # ── helpers ──
    @staticmethod
    def _mock_delta(spot: float, strike: float, right: OptionRight) -> float:
        # crude moneyness-based delta proxy
        moneyness = (spot - strike) / spot
        if right == OptionRight.CALL:
            return round(max(0.05, min(0.95, 0.5 + moneyness * 2)), 3)
        return round(max(-0.95, min(-0.05, -0.5 + moneyness * 2)), 3)

    @staticmethod
    def _occ_symbol(under: str, exp: datetime, right: OptionRight, strike: float) -> str:
        date_str = exp.strftime("%y%m%d")
        r = "C" if right == OptionRight.CALL else "P"
        strike_int = int(round(strike * 1000))
        return f"{under}{date_str}{r}{strike_int:08d}"

    @staticmethod
    def _parse_occ(occ: str) -> tuple[str, datetime, OptionRight, float]:
        # Very forgiving parser: find the date portion.
        # OCC format: SYMBOL[YYMMDD][C|P][STRIKE*1000 padded to 8]
        for i in range(1, len(occ) - 9):
            chunk = occ[i:i + 6]
            if chunk.isdigit():
                try:
                    dt = datetime.strptime(chunk, "%y%m%d").replace(tzinfo=timezone.utc)
                    under = occ[:i]
                    right = OptionRight.CALL if occ[i + 6] == "C" else OptionRight.PUT
                    strike = int(occ[i + 7:i + 15]) / 1000.0
                    return under, dt, right, strike
                except ValueError:
                    continue
        raise ValueError(f"Cannot parse OCC symbol: {occ}")
