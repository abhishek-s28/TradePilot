"""Signal service: orchestrates strategies across a watchlist."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.logging import get_logger
from app.data.factory import get_provider
from app.database.models import SignalRow, StrategyConfig
from app.database.session import session_factory
from app.market.session import MarketSessionInfo, classify_us_equity_session
from app.models.domain import AssetClass, Direction, OptionContract, OptionRight, Signal
from app.strategies.base import StrategyContext, registry
from app.strategies.regime import detect_regime
from app.utils.indicators import bars_to_frame

# Force-import strategies so they self-register via @registry.register
from app.strategies import (  # noqa: F401
    advanced,
    gamma_scalp,
    iv_crush,
    lifecycle,
    macd_crossover,
    mean_reversion,
    momentum_breakout,
    opening_range_breakout,
    stat_arb,
    vwap_deviation,
)
from app.strategies.stat_arb import PAIRS, StatisticalArbitrageStrategy

log = get_logger(__name__)

# High-activity universe: US-only (Alpaca doesn't support TSX/international).
# Focused on high-beta, high-volume names that generate more signals.
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "SMH", "TLT", "GLD",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "JPM",
]
REGIME_SYMBOL = "SPY"


class SignalService:
    async def enabled_strategies(self) -> list[str]:
        async with session_factory()() as s:
            res = await s.execute(select(StrategyConfig).where(StrategyConfig.enabled.is_(True)))
            rows = res.scalars().all()
        if rows:
            return [r.name for r in rows]
        # default: all registered
        return list(registry.all().keys())

    async def scan(
        self,
        universe: list[str] | None = None,
        session_info: MarketSessionInfo | None = None,
        include_options_when_closed: bool = False,
    ) -> list[Signal]:
        import numpy as np

        provider = await get_provider()
        universe = universe or DEFAULT_UNIVERSE
        session_info = session_info or classify_us_equity_session()
        option_ideas_allowed = session_info.can_open_option or include_options_when_closed
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=10)

        # Regime via SPY bars
        spy_bars = await provider.get_bars(REGIME_SYMBOL, "5Min", start, now)
        regime = detect_regime(bars_to_frame(spy_bars))

        signals: list[Signal] = []
        enabled = await self.enabled_strategies()

        # Pre-fetch and cache bars for all symbols (reused by pairs scan)
        bars_cache: dict[str, object] = {}
        df_cache: dict[str, object] = {}

        for sym in universe:
            try:
                bars = await provider.get_bars(sym, "5Min", start, now)
                if bars:
                    bars_cache[sym] = bars
                    df_cache[sym] = bars_to_frame(bars)
            except Exception as exc:  # noqa: BLE001
                log.debug("signal_scan.bars_fetch_failed", symbol=sym, error=str(exc))

        for sym in universe:
            df = df_cache.get(sym)
            if df is None or len(df) == 0:
                continue
            try:
                quote = await provider.get_quote(sym)

                # Compute realized-vol rank proxy for IVCrushSellStrategy
                iv_rank_proxy = 0.0
                nearest_atm_dte = 999
                try:
                    returns = df["close"].pct_change().dropna()
                    if len(returns) >= 80:
                        rv_short = returns.rolling(20).std().iloc[-1]
                        rv_max = returns.rolling(20).std().rolling(60).max().iloc[-1]
                        if not np.isnan(rv_max) and rv_max > 0:
                            iv_rank_proxy = float(rv_short / rv_max)
                except Exception:
                    pass

                ctx = StrategyContext(
                    symbol=sym, bars=df, latest_quote=quote,
                    market_regime=regime, now=now,
                    extra={
                        "iv_rank_proxy": iv_rank_proxy,
                        "nearest_atm_dte": nearest_atm_dte,
                        "market_session": session_info.session.value,
                        "trading_phase": session_info.phase.value,
                    },
                )
                for sname in enabled:
                    cls = registry.get(sname)
                    if not cls:
                        continue
                    strat = cls()
                    issues = strat.validate()
                    if issues:
                        log.warning("strategy.invalid", strategy=sname, issues=issues)
                        continue
                    for raw_sig in strat.generate(ctx):
                        if (
                            raw_sig.asset_class == AssetClass.OPTION
                            and not option_ideas_allowed
                        ):
                            continue
                        sig = await _resolve_option_contract(raw_sig, provider, quote, now)
                        if sig is not None:
                            signals.append(sig)
                        if (
                            raw_sig.asset_class == AssetClass.STOCK
                            and raw_sig.suitable_for_options
                            and raw_sig.direction == Direction.BULLISH
                            and option_ideas_allowed
                        ):
                            call_sig = await _resolve_companion_call(raw_sig, provider, quote, now)
                            if call_sig is not None:
                                signals.append(call_sig)
            except Exception as exc:  # noqa: BLE001
                log.error("signal_scan.error", symbol=sym, error=str(exc))

        # ── Pairs (stat-arb) scan ────────────────────────────────────────────
        stat_strat = StatisticalArbitrageStrategy()
        pairs_issues = stat_strat.validate()
        if not pairs_issues:
            for sym_a, sym_b in PAIRS:
                df_a = df_cache.get(sym_a)
                df_b = df_cache.get(sym_b)
                if df_a is None or df_b is None:
                    continue
                try:
                    quote_a = await provider.get_quote(sym_a)
                    ctx_a = StrategyContext(
                        symbol=sym_a, bars=df_a, latest_quote=quote_a,
                        market_regime=regime, now=now,
                        extra={
                            "peer_symbol": sym_b,
                            "peer_bars": df_b,
                            "market_session": session_info.session.value,
                            "trading_phase": session_info.phase.value,
                        },
                    )
                    for raw_sig in stat_strat.generate(ctx_a):
                        if (
                            raw_sig.asset_class == AssetClass.OPTION
                            and not option_ideas_allowed
                        ):
                            continue
                        sig = await _resolve_option_contract(raw_sig, provider, quote_a, now)
                        if sig is not None:
                            signals.append(sig)
                except Exception as exc:  # noqa: BLE001
                    log.error("signal_scan.pairs_error", pair=f"{sym_a}/{sym_b}", error=str(exc))

        if signals:
            await self._persist(signals)
        log.info("signal_scan.done", count=len(signals), regime=regime)
        return signals

    async def _persist(self, signals: list[Signal]) -> None:
        async with session_factory()() as s:
            for sig in signals:
                row = SignalRow(
                    strategy=sig.strategy,
                    asset_class=sig.asset_class.value,
                    symbol=sig.symbol,
                    underlying=sig.underlying,
                    direction=sig.direction.value,
                    entry=sig.entry,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    confidence=sig.confidence,
                    reason=sig.reason,
                    invalidation=sig.invalidation,
                    risk_reward=sig.risk_reward,
                    suggested_qty=sig.suggested_qty,
                    suitable_for_options=sig.suitable_for_options,
                    holding_period_hint=sig.holding_period_hint,
                    status=sig.status.value,
                    generated_at=sig.generated_at,
                    payload=sig.metadata,
                )
                s.add(row)
                await s.flush()
                sig.id = row.id
            await s.commit()

    async def list_recent(self, limit: int = 50) -> list[dict]:
        async with session_factory()() as s:
            res = await s.execute(
                select(SignalRow).order_by(SignalRow.generated_at.desc()).limit(limit)
            )
            rows = res.scalars().all()
        return [
            {
                "id": r.id, "strategy": r.strategy, "asset_class": r.asset_class,
                "symbol": r.symbol, "direction": r.direction,
                "entry": r.entry, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
                "confidence": r.confidence, "reason": r.reason,
                "risk_reward": r.risk_reward, "status": r.status,
                "generated_at": r.generated_at.isoformat(), "metadata": r.payload,
            }
            for r in rows
        ]


async def _resolve_option_contract(
    signal: Signal,
    provider,
    underlying_quote,
    now: datetime,
) -> Signal | None:
    """Turn an underlying-level option idea into a real OCC contract signal."""
    if signal.asset_class != AssetClass.OPTION:
        return signal
    if _looks_like_occ(signal.symbol):
        return signal

    underlying = signal.underlying or signal.symbol
    right = OptionRight.PUT if signal.direction == Direction.BEARISH else OptionRight.CALL
    spot = float(getattr(underlying_quote, "mid", 0) or getattr(underlying_quote, "last", 0) or 0)
    try:
        chain = await provider.get_options_chain(underlying, spot=spot)
    except Exception as exc:  # noqa: BLE001
        log.warning("option_resolve.chain_failed", symbol=underlying, error=str(exc))
        return None
    contract = _select_contract(chain, right, spot, now)
    if contract is None or contract.mid <= 0:
        log.info("option_resolve.no_contract", symbol=underlying, right=right.value)
        return None

    entry = round(contract.mid, 2)
    stop = round(max(0.01, entry * 0.50), 2)
    target = round(entry * 1.90, 2)
    rr = round((target - entry) / max(entry - stop, 0.01), 2)
    metadata = {
        **signal.metadata,
        "underlying_symbol": underlying,
        "underlying_entry": signal.entry,
        "underlying_stop_loss": signal.stop_loss,
        "underlying_take_profit": signal.take_profit,
        "selected_option": {
            "symbol": contract.symbol,
            "right": contract.right.value,
            "expiration": contract.expiration.isoformat(),
            "strike": contract.strike,
            "bid": contract.bid,
            "ask": contract.ask,
            "mid": entry,
            "spread_pct": round(contract.spread_pct, 4),
            "volume": contract.volume,
            "open_interest": contract.open_interest,
            "delta": contract.delta,
            "liquidity_score": contract.liquidity_score,
        },
    }
    return signal.model_copy(update={
        "symbol": contract.symbol,
        "underlying": underlying,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "risk_reward": rr,
        "reason": f"{signal.reason} Selected {right.value} contract {contract.symbol} at {entry:.2f}.",
        "invalidation": f"Option premium below {stop:.2f} or underlying thesis invalidates.",
        "metadata": metadata,
    })


def _select_contract(
    chain: list[OptionContract],
    right: OptionRight,
    spot: float,
    now: datetime,
) -> OptionContract | None:
    candidates: list[OptionContract] = []
    for contract in chain:
        dte = (contract.expiration.date() - now.date()).days
        if contract.right != right:
            continue
        if dte < 14 or dte > 45:
            continue
        if contract.mid < 0.35:
            continue
        if contract.spread_pct > 0.12:
            continue
        if contract.delta is not None:
            delta = abs(contract.delta)
            if delta < 0.30 or delta > 0.65:
                continue
        candidates.append(contract)

    if not candidates:
        return None

    target_delta = 0.45
    def _rank(c: OptionContract) -> tuple[float, float, float, float]:
        d = abs(c.delta) if c.delta is not None else target_delta
        delta_penalty = abs(d - target_delta)
        moneyness_penalty = abs(c.strike - spot) / max(spot, 1.0) if spot > 0 else 0
        return (
            delta_penalty,
            c.spread_pct,
            moneyness_penalty,
            -c.liquidity_score,
        )

    return sorted(candidates, key=_rank)[0]


async def _resolve_companion_call(
    signal: Signal,
    provider,
    underlying_quote,
    now: datetime,
) -> Signal | None:
    """Generate a companion CALL option signal from a bullish stock signal."""
    from app.models.domain import AssetClass as AC, Direction as D
    option_version = signal.model_copy(update={
        "asset_class": AC.OPTION,
        "underlying": signal.symbol,
        "direction": D.BULLISH,
    })
    return await _resolve_option_contract(option_version, provider, underlying_quote, now)


def _looks_like_occ(symbol: str) -> bool:
    return len(symbol) > 8 and any(c.isdigit() for c in symbol)
