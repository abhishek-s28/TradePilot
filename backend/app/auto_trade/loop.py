"""Async auto-trade loop for Alpaca paper trading.

Regime-aware dynamic scaling: when SPY is in a confirmed bullish trend the loop
raises the per-cycle entry cap and open-position limit proportionally, then
restores conservative defaults the moment the regime turns choppy, bearish, or
high-volatility. This means the bot naturally trades more when conditions are
favourable and pulls back when they are not.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from app.auto_trade.option_exit import OptionExitDecision, evaluate_option_exit
from app.auto_trade.watchlist import derived_watchlist
from app.brokers.base import BrokerAdapter, BrokerOrder
from app.brokers.factory import get_broker, reset_broker
from app.core.logging import get_logger
from app.core.settings import TradingMode, get_settings
from app.data.factory import get_provider
from app.database.models import OrderRow, RiskSettings, SystemEvent
from app.database.session import session_factory
from app.market.session import MarketSession, classify_us_equity_session
from app.models.domain import (
    AssetClass,
    OrderProposal as DomainOrderProposal,
    OrderType,
    TimeInForce,
)
from app.risk.loader import load_risk_config
from app.risk.manager import OrderProposal, RiskManager
from app.risk.runtime import load_runtime_risk_context
from app.services.auto_trader import is_auto_trading_enabled
from app.services.order_journal import record_broker_order
from app.strategies import STRATEGY_REGISTRY
from app.strategies.alpaca_auto import BaseStrategy
from app.utils.indicators import atr, ema

# Fallback exit policy for positions whose opening order can't be matched to a
# strategy in OrderRow (e.g. pre-existing/manually-opened positions).
_DEFAULT_EXIT_STRATEGY = BaseStrategy("default_exit")
_NY = ZoneInfo("America/New_York")
_EOD_FLATTEN_TIME = time(15, 50)
_EOD_NO_NEW_ENTRIES_TIME = time(15, 45)

_ENTRY_STRATEGY_ALLOWLIST = frozenset({
    # Directional premium-buying strategies
    "zero_dte_scalp",
    "weekly_momentum",
    "supply_demand_reversal",
    "volatility_breakout",
    "mean_reversion_options",
    "news_catalyst",
    "insider_buy_signal",
    "analyst_upgrade",
    "vwap_bounce",
    "rsi_pullback",
    "momentum_scalp",
    "long_directional",
    # New high-conviction day trading strategies
    "opening_drive",
    "consolidation_breakout",
    "macd_power_cross",
    "multi_confluence",
    # Debit spreads (directional, capped risk)
    "bull_call_spread",
    "bear_put_spread",
    # Credit spreads (theta-positive, profit from time decay)
    "bull_put_spread",
    "bear_call_spread",
    # Neutral / premium-selling structures
    "iron_condor",
    "iron_butterfly",
    "calendar_spread",
    "iv_crush_strangle",
})

log = get_logger(__name__)


# ── Regime detection ──────────────────────────────────────────────────────────

async def _detect_market_regime(provider) -> str:
    """Classify the broad market using SPY 5-min bars + momentum scoring.

    Returns one of: strong_bull | bullish | bearish | strong_bear | choppy | high_vol | unknown

    Uses multiple factors:
    - EMA alignment (trend direction)
    - ATR expansion (volatility)
    - Recent momentum (last 10 bars direction)
    - Price relative to VWAP (intraday bias)
    - Realized volatility (risk environment)
    """
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=10)
        from app.models.domain import Bar
        bars: list[Bar] = await provider.get_bars("SPY", "5Min", start, end)
        if len(bars) < 50:
            return "unknown"
        df = pd.DataFrame([
            {"high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ])
        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema9 = float(ema(close, 9).iloc[-1])
        ema20 = float(ema(close, 20).iloc[-1])
        ema50 = float(ema(close, 50).iloc[-1])
        last = float(close.iloc[-1])
        last_atr = float(atr(high, low, close, 14).iloc[-1])
        atr_pct = last_atr / last if last else 0

        import numpy as np
        recent_returns = close.pct_change().dropna().tail(20)
        realized_vol = float(np.std(recent_returns)) * (252 ** 0.5)

        if realized_vol > 0.40 or atr_pct > 0.025:
            return "high_vol"

        recent_momentum = float(close.iloc[-1] / close.iloc[-10] - 1) if len(close) >= 10 else 0.0

        if last > ema9 > ema20 > ema50 and recent_momentum > 0.002:
            return "strong_bull"
        if last > ema20 > ema50:
            return "bullish"
        if last < ema9 < ema20 < ema50 and recent_momentum < -0.002:
            return "strong_bear"
        if last < ema20 < ema50:
            return "bearish"
        return "choppy"
    except Exception as exc:
        log.warning("regime_detect.failed", error=str(exc))
        return "unknown"


_PREMIUM_BUYING_STRATEGIES = frozenset({
    "zero_dte_scalp", "weekly_momentum", "long_directional",
    "volatility_breakout", "mean_reversion_options", "news_catalyst",
    "supply_demand_reversal", "insider_buy_signal", "analyst_upgrade",
    "vwap_bounce", "rsi_pullback", "momentum_scalp",
    "bull_call_spread", "bear_put_spread",
    "opening_drive", "consolidation_breakout", "macd_power_cross",
    "multi_confluence",
})

_SHORT_DTE_STRATEGIES = frozenset({
    "zero_dte_scalp", "vwap_bounce", "rsi_pullback", "momentum_scalp",
})


def _regime_risk_multipliers(regime: str) -> dict:
    """Return cap ceilings for the current market regime.

    Strong trends = more entries. Choppy/high-vol = aggressive cut.
    Day traders make money in trending markets and lose in chop.
    """
    return {
        "strong_bull": {"max_entries": 6, "max_open": 15, "confidence_adj": -0.03},
        "bullish":     {"max_entries": 5, "max_open": 12, "confidence_adj": -0.01},
        "bearish":     {"max_entries": 4, "max_open": 10, "confidence_adj": +0.02},
        "strong_bear": {"max_entries": 4, "max_open": 10, "confidence_adj": +0.02},
        "high_vol":    {"max_entries": 2, "max_open": 6,  "confidence_adj": +0.08},
        "choppy":      {"max_entries": 2, "max_open": 6,  "confidence_adj": +0.07},
        "unknown":     {"max_entries": 2, "max_open": 5,  "confidence_adj": +0.08},
    }.get(regime, {"max_entries": 2, "max_open": 5, "confidence_adj": +0.08})


def _strategy_allowed_for_regime(strategy_name: str, regime: str) -> bool:
    if regime in ("choppy", "high_vol", "unknown"):
        if strategy_name in _SHORT_DTE_STRATEGIES:
            return False
        if strategy_name in _PREMIUM_BUYING_STRATEGIES and regime == "high_vol":
            return False
    return True


class AutoTradeLoop:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._scan_task: asyncio.Task | None = None
        self._exit_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._scan_task and not self._scan_task.done():
            return
        self._stopping.clear()
        self._scan_task = asyncio.create_task(self._run_scans(), name="auto-trade-scan-loop")
        self._exit_task = asyncio.create_task(self._run_exits(), name="auto-trade-exit-loop")

    async def stop(self) -> None:
        self._stopping.set()
        tasks = [t for t in (self._scan_task, self._exit_task) if t is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    async def _run_scans(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.scan_and_trade()
            except Exception as exc:  # noqa: BLE001
                log.error("auto_trade_loop.scan_failed", error=str(exc))
                await self._reconnect_with_backoff()

            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.auto_trade_scan_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _run_exits(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.exit_check()
            except Exception as exc:  # noqa: BLE001
                log.error("auto_trade_loop.exit_failed", error=str(exc))
                await self._reconnect_with_backoff()

            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.auto_trade_exit_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def scan_and_trade(self) -> dict:
        if self.settings.trading_mode != TradingMode.AUTO:
            return {"skipped": True, "reason": "trading_mode_not_auto"}
        if not await is_auto_trading_enabled():
            return {"skipped": True, "reason": "auto_trading_disabled_or_kill_switch"}

        session = classify_us_equity_session()
        if not self._session_enabled(session.session):
            log.info("auto_trade_loop.session_skip", session=session.session.value)
            return {"skipped": True, "reason": f"session_disabled:{session.session.value}"}
        if session.session == MarketSession.OVERNIGHT:
            log.info(
                "auto_trade_loop.overnight_inert",
                reason="No liquid retail overnight session for this options-first bot.",
            )
            return {"skipped": True, "reason": "overnight_inert"}

        now_ny = datetime.now(timezone.utc).astimezone(_NY)
        if now_ny.time() >= _EOD_NO_NEW_ENTRIES_TIME:
            log.info("auto_trade_loop.eod_no_new_entries")
            return {"skipped": True, "reason": "eod_no_new_entries"}

        broker = await get_broker()
        provider = await get_provider()
        risk_context = await load_runtime_risk_context(broker, session_info=session)
        account = risk_context.broker_account
        positions = risk_context.positions
        risk_account = risk_context.risk_account
        config = await load_risk_config(equity=account.equity)
        state = risk_context.state

        preflight = RiskManager().evaluate_order(
            OrderProposal(
                strategy_name="preflight",
                legs=[],
                symbol="",
                max_risk_usd=0.0,
                est_cost_usd=0.0,
                signal_values={},
                confidence=1.0,
            ),
            risk_account,
            positions,
            state,
            config,
        )
        if preflight.cancel_pending_orders:
            await self._cancel_pending_orders(broker)
            daily_pnl = risk_account.realized_pnl_today + risk_account.unrealized_pnl_today
            await self._persist_risk_halt(daily_pnl, "daily_loss_limit_hit")
            await self._event(
                "risk_halt",
                "DAILY LOSS LIMIT HIT - pending orders canceled; auto-trading disabled.",
                {"daily_pnl": daily_pnl, "auto_trading_enabled": False, "kill_switch_active": True},
                "critical",
            )
            return {"skipped": True, "reason": "daily_loss_limit_hit"}

        # ── Regime detection — scale caps dynamically ─────────────────────────
        regime = await _detect_market_regime(provider)
        regime_mults = _regime_risk_multipliers(regime)
        remaining_trades = max(0, config.max_trades_per_day - state.trades_today)
        remaining_open = max(0, config.max_open_positions - len(positions))
        effective_max_entries = min(
            self.settings.auto_trade_max_entries_per_cycle,
            config.max_trades_per_day,
            remaining_trades,
            remaining_open,
            regime_mults["max_entries"],
        )
        effective_max_open = min(config.max_open_positions, regime_mults["max_open"])
        base_floor = _confidence_floor(config, state.market_session)
        tod_adj = _time_of_day_confidence_adj(now_ny)
        confidence_floor = max(
            base_floor + regime_mults["confidence_adj"] + tod_adj,
            base_floor,
        )

        log.info(
            "auto_trade_loop.regime",
            regime=regime,
            effective_max_entries=effective_max_entries,
            effective_max_open=effective_max_open,
            confidence_floor=round(confidence_floor, 3),
        )

        if effective_max_entries <= 0:
            return {
                "skipped": True,
                "reason": "entry_capacity_exhausted",
                "open_positions": len(positions),
                "trades_today": state.trades_today,
                "max_open_positions": config.max_open_positions,
                "max_trades_per_day": config.max_trades_per_day,
                "regime": regime,
            }

        symbols = await derived_watchlist()
        proposals: list[OrderProposal] = []
        account_ctx = SimpleNamespace(
            **risk_account.__dict__,
            positions=positions,
        )

        for symbol in symbols:
            for strategy_name, strategy in STRATEGY_REGISTRY.items():
                if strategy_name not in _ENTRY_STRATEGY_ALLOWLIST:
                    continue
                if not _strategy_allowed_for_regime(strategy_name, regime):
                    continue
                if getattr(strategy, "min_equity", 0.0) > account.equity:
                    continue
                try:
                    found = await strategy.scan(symbol, account_ctx, provider)
                    # Filter by regime-adjusted confidence floor before extending
                    found = [p for p in found if p.confidence >= confidence_floor]
                    proposals.extend(found)
                    for proposal in found:
                        log.info(
                            "auto_trade_loop.proposal",
                            strategy=proposal.strategy_name,
                            symbol=proposal.symbol,
                            legs=proposal.legs,
                            confidence=proposal.confidence,
                            max_risk_usd=proposal.max_risk_usd,
                            regime=regime,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "auto_trade_loop.symbol_strategy_failed",
                        symbol=symbol,
                        strategy=getattr(strategy, "name", "unknown"),
                        error=str(exc),
                    )

        proposals.sort(key=lambda p: p.confidence, reverse=True)
        proposals = _dedupe_by_underlying(proposals)
        submitted = 0
        rejected = 0
        for proposal in proposals:
            if submitted >= effective_max_entries:
                log.info(
                    "auto_trade_loop.rejected",
                    strategy=proposal.strategy_name,
                    symbol=proposal.symbol,
                    reason="cycle_entry_cap",
                )
                rejected += 1
                continue
            if len(positions) + submitted >= effective_max_open:
                log.info(
                    "auto_trade_loop.rejected",
                    strategy=proposal.strategy_name,
                    symbol=proposal.symbol,
                    reason="open_position_cap",
                )
                rejected += 1
                continue

            decision = RiskManager().evaluate_order(proposal, risk_account, positions, state, config)
            if not decision.approved:
                log.info(
                    "auto_trade_loop.rejected",
                    strategy=proposal.strategy_name,
                    symbol=proposal.symbol,
                    reasons=decision.reasons,
                )
                rejected += 1
                continue

            try:
                domain_proposal = _to_domain_order(proposal, decision.risk_score)
                broker, order = await self._place_order_with_reconnect(broker, domain_proposal)
                await record_broker_order(account=broker.name, proposal=domain_proposal, order=order)
                await self._event(
                    "auto_trade",
                    f"{proposal.strategy_name} submitted {proposal.legs}",
                    {
                        "strategy": proposal.strategy_name,
                        "legs": proposal.legs,
                        "signal_values": proposal.signal_values,
                        "computed_risk": {
                            "max_risk_usd": proposal.max_risk_usd,
                            "est_cost_usd": proposal.est_cost_usd,
                            "risk_score": decision.risk_score,
                        },
                        "fill": {
                            "order_id": order.id,
                            "status": order.status.value,
                            "filled_qty": order.filled_qty,
                            "avg_fill_price": order.avg_fill_price,
                        },
                    },
                )
                submitted += 1
                state.trades_today += 1
                if proposal.symbol:
                    state.pending_symbols.add(proposal.symbol)
                if proposal.underlying:
                    state.pending_symbols.add(proposal.underlying)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "auto_trade_loop.submit_failed",
                    strategy=proposal.strategy_name,
                    symbol=proposal.symbol,
                    error=str(exc),
                )
                await self._reconnect_with_backoff()

        return {
            "skipped": False,
            "proposals": len(proposals),
            "submitted": submitted,
            "rejected": rejected,
            "watchlist": len(symbols),
            "regime": regime,
            "effective_max_entries": effective_max_entries,
        }

    async def exit_check(self) -> dict:
        if self.settings.trading_mode != TradingMode.AUTO:
            return {"skipped": True, "reason": "trading_mode_not_auto"}
        if not await is_auto_trading_enabled():
            return {"skipped": True, "reason": "auto_trading_disabled_or_kill_switch"}

        broker = await get_broker()
        account = await broker.get_account()
        provider = await get_provider()
        positions = await broker.get_positions()
        account_ctx = SimpleNamespace(**account.model_dump(), positions=positions)
        closed = 0
        now_ny = datetime.now(timezone.utc).astimezone(_NY)
        eod_flatten = now_ny.time() >= _EOD_FLATTEN_TIME
        for position in positions:
            strategy, opened_at, order_row = await self._entry_context(position.symbol)
            if opened_at is not None:
                position.opened_at = opened_at
            try:
                should_exit = await strategy.should_exit(position, account_ctx, provider)
                reason = strategy.name
                metrics: dict = {}
                if position.asset_class == AssetClass.OPTION:
                    decision = evaluate_option_exit(
                        position,
                        opened_at=opened_at,
                        previous_peak_pnl_pct=_order_peak_pnl_pct(order_row),
                    )
                    await self._store_option_exit_state(order_row, decision)
                    should_exit = should_exit or decision.should_exit
                    reason = decision.reason if decision.should_exit else reason
                    metrics = {
                        "pnl_pct": decision.pnl_pct,
                        "peak_pnl_pct": decision.peak_pnl_pct,
                        "dte": decision.dte,
                        "held_minutes": decision.held_minutes,
                        "current_value": decision.current_value,
                        "cost_basis": decision.cost_basis,
                    }

                if eod_flatten and not should_exit:
                    should_exit = True
                    reason = "eod_flatten"

                if should_exit:
                    order = await broker.close_position(position.symbol)
                    if order:
                        closed += 1
                        await self._event(
                            "auto_exit",
                            f"Closed {position.symbol}: {reason}",
                            {
                                "order_id": order.id,
                                "status": order.status.value,
                                "symbol": position.symbol,
                                "reason": reason,
                                "asset_class": position.asset_class.value,
                                **metrics,
                            },
                        )
            except Exception as exc:  # noqa: BLE001
                log.warning("auto_trade_loop.exit_check_failed", symbol=position.symbol, error=str(exc))
        return {"checked": len(positions), "closed": closed}

    async def _persist_risk_halt(self, daily_pnl: float, reason: str) -> None:
        async with session_factory()() as s:
            res = await s.execute(select(RiskSettings).limit(1))
            row = res.scalar_one_or_none()
            if row is None:
                row = RiskSettings()
                s.add(row)
                await s.flush()
            row.kill_switch_active = True
            row.auto_trading_enabled = False
            s.add(SystemEvent(
                kind="risk_halt_persisted",
                message="Auto-trading disabled after risk halt.",
                payload={"daily_pnl": daily_pnl, "reason": reason},
                severity="critical",
            ))
            await s.commit()

    async def _entry_context(self, symbol: str) -> tuple[BaseStrategy, datetime | None, OrderRow | None]:
        """Find the order that opened this position.

        Returns the strategy that placed the entry order (so ITS OWN
        stop-loss/take-profit applies — not whichever of the registered
        strategies' thresholds happens to trigger first when iterated in
        registry order, the prior bug: every position inherited the tightest
        stop in the whole registry regardless of which strategy opened it)
        plus the order's fill/submit timestamp.

        Not filtered to status == "filled": the Alpaca adapter records
        `order.status` at submission time and never gets updated once the
        broker fills it asynchronously, so "filled" rows essentially never
        exist even for positions that are clearly open. For a currently-open
        position, the most recent order on that symbol IS the entry order —
        close_position() doesn't journal a row — so this is a safe proxy.
        """
        async with session_factory()() as s:
            res = await s.execute(
                select(OrderRow)
                .where(OrderRow.symbol == symbol)
                .order_by(OrderRow.created_at.desc())
                .limit(1)
            )
            row = res.scalar_one_or_none()
        if row is None:
            return _DEFAULT_EXIT_STRATEGY, None, None
        name = (row.payload or {}).get("strategy")
        strategy = STRATEGY_REGISTRY.get(name, _DEFAULT_EXIT_STRATEGY)
        opened_at = row.filled_at or row.submitted_at or row.created_at
        return strategy, opened_at, row

    async def _store_option_exit_state(
        self,
        row: OrderRow | None,
        decision: OptionExitDecision,
    ) -> None:
        if row is None:
            return
        payload = dict(row.payload or {})
        state = dict(payload.get("option_exit_state") or {})
        if decision.peak_pnl_pct >= float(state.get("peak_pnl_pct", decision.peak_pnl_pct)):
            state["peak_pnl_pct"] = decision.peak_pnl_pct
        state.update({
            "last_pnl_pct": decision.pnl_pct,
            "last_dte": decision.dte,
            "last_reason": decision.reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        payload["option_exit_state"] = state
        async with session_factory()() as s:
            res = await s.execute(select(OrderRow).where(OrderRow.id == row.id))
            fresh = res.scalar_one_or_none()
            if fresh is not None:
                fresh.payload = payload
                await s.commit()

    async def _cancel_pending_orders(self, broker) -> None:
        for order in await broker.get_orders():
            if order.status.value in {"pending", "submitted", "partially_filled"}:
                await broker.cancel_order(order.id)

    async def _place_order_with_reconnect(
        self,
        broker: BrokerAdapter,
        proposal: DomainOrderProposal,
    ) -> tuple[BrokerAdapter, BrokerOrder]:
        try:
            return broker, await broker.place_order(proposal)
        except Exception as exc:  # noqa: BLE001
            if "not connected" not in str(exc).lower():
                raise
            log.warning(
                "auto_trade_loop.submit_reconnecting",
                symbol=proposal.symbol,
                error=str(exc),
            )
            broker = await self._reconnect_with_backoff()
            return broker, await broker.place_order(proposal)

    async def _reconnect_with_backoff(self) -> BrokerAdapter:
        for delay in (1, 2, 4):
            try:
                return await reset_broker()
            except Exception as exc:  # noqa: BLE001
                log.warning("auto_trade_loop.reconnect_failed", delay=delay, error=str(exc))
                await asyncio.sleep(delay)
        return await get_broker()

    def _session_enabled(self, session: MarketSession) -> bool:
        if session == MarketSession.CLOSED:
            return False
        if session == MarketSession.REGULAR:
            return self.settings.auto_trade_regular_hours
        if session == MarketSession.PREMARKET:
            return self.settings.auto_trade_premarket
        if session == MarketSession.AFTERHOURS:
            return self.settings.auto_trade_afterhours
        if session == MarketSession.OVERNIGHT:
            return self.settings.auto_trade_overnight
        return False

    async def _event(self, kind: str, message: str, payload: dict, severity: str = "info") -> None:
        async with session_factory()() as session:
            session.add(SystemEvent(kind=kind, message=message, payload=payload, severity=severity))
            await session.commit()


def _to_domain_order(proposal: OrderProposal, risk_score: float) -> DomainOrderProposal:
    symbol = proposal.symbol or (proposal.legs[0] if proposal.legs else "")
    return DomainOrderProposal(
        strategy_name=proposal.strategy_name,
        signal_id=None,
        symbol=symbol,
        asset_class=proposal.asset_class,
        side=proposal.side,
        qty=proposal.qty,
        legs=proposal.legs,
        order_type=OrderType.LIMIT if proposal.limit_price is not None else OrderType.MARKET,
        limit_price=proposal.limit_price,
        time_in_force=TimeInForce.DAY,
        extended_hours=proposal.extended_hours,
        estimated_cost=proposal.est_cost_usd,
        estimated_max_loss=proposal.max_risk_usd,
        max_risk_usd=proposal.max_risk_usd,
        est_cost_usd=proposal.est_cost_usd,
        signal_values=proposal.signal_values,
        confidence=proposal.confidence,
        reason=proposal.reason,
        risk_score=risk_score,
    )


def _confidence_floor(config, session: str) -> float:
    if session in {"premarket", "afterhours"}:
        return float(config.min_confidence_extended)
    if session == "overnight":
        return float(config.min_confidence_overnight)
    return float(config.min_confidence_regular)


def _dedupe_by_underlying(proposals: list[OrderProposal]) -> list[OrderProposal]:
    best: list[OrderProposal] = []
    seen: set[str] = set()
    for proposal in proposals:
        key = (proposal.underlying or _root_symbol(proposal.symbol)) + ":" + proposal.strategy_name
        if key in seen:
            continue
        seen.add(key)
        best.append(proposal)
    return best


def _root_symbol(symbol: str) -> str:
    for i in range(1, len(symbol) - 14):
        chunk = symbol[i:i + 6]
        right = symbol[i + 6:i + 7]
        strike = symbol[i + 7:i + 15]
        if right in {"C", "P"} and chunk.isdigit() and strike.isdigit():
            return symbol[:i]
    return symbol


def _time_of_day_confidence_adj(now_ny: datetime) -> float:
    """Adjust confidence floor based on time of day.

    Day trading rule: the best setups happen in the first 90 minutes
    (9:30-11:00 ET) when volume and volatility are highest. The midday
    lull (11:00-14:00) is chop city — raise the bar. Late afternoon
    (14:00-15:00) can produce moves but requires more conviction.
    After 15:00 is too close to EOD to enter new positions.
    """
    t = now_ny.time()
    if t < time(11, 0):
        return -0.02
    if t < time(14, 0):
        return +0.05
    if t < time(15, 0):
        return +0.03
    return +0.08


def _order_peak_pnl_pct(row: OrderRow | None) -> float | None:
    if row is None or not row.payload:
        return None
    state = row.payload.get("option_exit_state") or {}
    try:
        return float(state.get("peak_pnl_pct"))
    except (TypeError, ValueError):
        return None
