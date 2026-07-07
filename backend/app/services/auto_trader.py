"""AutoTrader — the core autonomous trading engine.

Lifecycle per scheduler tick:
  1. scan() → fresh signals from all enabled strategies
  2. For each signal: build a live RiskState from DB + call RiskManager
  3. If approved → place_order() on the configured broker
  4. Persist result in SystemEvent log

Guards:
  - auto_trading_enabled must be True in risk_settings
  - kill_switch_active blocks everything (RiskManager checks this)
  - Duplicate-position prevention (RiskManager checks this)
  - All normal risk limits apply identically to manual paper trades
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.brokers.factory import get_broker
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.data.factory import get_provider
from app.database.models import RiskSettings, SignalRow, SystemEvent
from app.database.session import session_factory
from app.market.session import MarketSession, MarketSessionInfo, classify_us_equity_session
from app.models.domain import (
    AssetClass, OrderStatus, Signal, SignalStatus
)
from app.risk.loader import load_risk_config
from app.risk.manager import RiskManager, RiskState
from app.risk.runtime import load_runtime_risk_context
from app.services.order_journal import record_broker_order
from app.services.signal_service import SignalService

log = get_logger(__name__)


@dataclass
class AutoTradeResult:
    signal_id: str
    symbol: str
    approved: bool
    reasons: list[str]
    order_id: str | None = None
    fill_price: float | None = None
    order_status: str | None = None


async def is_auto_trading_enabled() -> bool:
    """Read the live flag from DB (not cached — always authoritative)."""
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
    return bool(row and row.auto_trading_enabled and not row.kill_switch_active)


async def _load_live_risk_state(session_info: MarketSessionInfo) -> RiskState:
    """Compute the live RiskState by querying the DB for today's activity."""
    broker = await get_broker()
    return (await load_runtime_risk_context(broker, session_info=session_info)).state


async def auto_execute_signals(
    signals: list[Signal],
    session_info: MarketSessionInfo | None = None,
) -> list[AutoTradeResult]:
    """
    Route a fresh list of signals through the risk manager and, if approved,
    submit them to the configured broker.
    """
    if not signals:
        return []

    settings = get_settings()
    session_info = session_info or classify_us_equity_session()
    broker = await get_broker()
    account = await broker.get_account()
    config = await load_risk_config(equity=account.equity)
    state = await _load_live_risk_state(session_info)
    provider = await get_provider()
    rm = RiskManager()
    results: list[AutoTradeResult] = []
    submitted_this_cycle = 0

    # Track symbols we've already approved this batch (intra-batch dedup)
    batch_approved_symbols: set[str] = set()
    batch_approved_underlyings: set[str] = set()

    for signal in signals:
        underlying_key = signal.underlying or signal.symbol
        if signal.symbol in batch_approved_symbols:
            results.append(AutoTradeResult(
                signal_id=signal.id or "",
                symbol=signal.symbol,
                approved=False,
                reasons=["duplicate_in_batch"],
            ))
            continue
        if underlying_key in batch_approved_underlyings:
            results.append(AutoTradeResult(
                signal_id=signal.id or "",
                symbol=signal.symbol,
                approved=False,
                reasons=["duplicate_underlying_in_batch"],
            ))
            continue

        try:
            if submitted_this_cycle >= settings.auto_trade_max_entries_per_cycle:
                results.append(AutoTradeResult(
                    signal_id=signal.id or "",
                    symbol=signal.symbol,
                    approved=False,
                    reasons=["cycle_entry_cap"],
                ))
                continue

            # Fetch a fresh quote right before risk evaluation.
            # For options: if the symbol is not an OCC string (i.e. it IS the
            # underlying ticker), fall back to a stock quote so risk evaluation
            # can compute spread/price correctly.
            from app.models.domain import Quote
            underlying_sym = signal.underlying or signal.symbol
            is_occ = (
                signal.asset_class == AssetClass.OPTION
                and len(signal.symbol) > 6
                and any(c.isdigit() for c in signal.symbol)
            )
            if signal.asset_class == AssetClass.STOCK or not is_occ:
                quote = await provider.get_quote(underlying_sym)
                # Re-label quote with the signal's symbol so RiskManager finds it
                quote = Quote(
                    symbol=signal.symbol,
                    bid=quote.bid,
                    ask=quote.ask,
                    last=quote.last,
                    bid_size=quote.bid_size,
                    ask_size=quote.ask_size,
                    timestamp=quote.timestamp,
                )
            else:
                contract = await provider.get_option_quote(signal.symbol)
                quote = Quote(
                    symbol=signal.symbol,
                    bid=contract.bid,
                    ask=contract.ask,
                    last=contract.last,
                    timestamp=datetime.now(timezone.utc),
                )

            decision = rm.evaluate_signal(
                signal=signal,
                quote=quote,
                account=account,
                state=state,
                config=config,
            )

            if not decision.approved or decision.proposal is None:
                results.append(AutoTradeResult(
                    signal_id=signal.id or "",
                    symbol=signal.symbol,
                    approved=False,
                    reasons=decision.reasons,
                ))
                log.info(
                    "auto_trader.rejected",
                    symbol=signal.symbol,
                    reasons=decision.reasons,
                )
                continue

            proposal = decision.proposal
            if proposal.extended_hours:
                proposal = proposal.model_copy(update={
                    "extended_hours": True,
                    "order_type": proposal.order_type,
                })

            order = await broker.place_order(proposal)
            await record_broker_order(account=broker.name, proposal=proposal, order=order)

            # Update signal status in DB
            async with session_factory()() as s:
                res = await s.execute(
                    select(SignalRow).where(SignalRow.id == signal.id)
                )
                row = res.scalar_one_or_none()
                if row:
                    row.status = (
                        SignalStatus.PAPER_EXECUTED.value
                        if order.status == OrderStatus.FILLED
                        else SignalStatus.APPROVED.value
                    )
                    await s.commit()

            # Log to system events
            fill_text = (
                f"filled @ {order.avg_fill_price}"
                if order.status == OrderStatus.FILLED
                else f"submitted ({order.status.value})"
            )
            async with session_factory()() as s:
                s.add(SystemEvent(
                    kind="auto_trade",
                    message=(
                        f"AUTO: {signal.direction.value.upper()} {signal.symbol} "
                        f"x{proposal.qty} {fill_text} "
                        f"[{signal.strategy}] {session_info.session.value} conf={signal.confidence:.0%}"
                    ),
                    payload={
                        "signal_id": signal.id,
                        "symbol": signal.symbol,
                        "strategy": signal.strategy,
                        "direction": signal.direction.value,
                        "qty": proposal.qty,
                        "fill_price": order.avg_fill_price,
                        "stop_loss": proposal.stop_loss,
                        "take_profit": proposal.take_profit,
                        "confidence": signal.confidence,
                        "order_id": order.id,
                        "order_status": order.status.value,
                        "market_session": session_info.session.value,
                        "extended_hours": proposal.extended_hours,
                    },
                    severity="info",
                ))
                await s.commit()

            batch_approved_symbols.add(signal.symbol)
            batch_approved_underlyings.add(underlying_key)
            # Update intra-batch state counters
            state.trades_today += 1
            state.pending_symbols.add(signal.symbol)
            state.pending_symbols.add(underlying_key)
            submitted_this_cycle += 1

            result = AutoTradeResult(
                signal_id=signal.id or "",
                symbol=signal.symbol,
                approved=True,
                reasons=[],
                order_id=order.id,
                fill_price=order.avg_fill_price,
                order_status=order.status.value,
            )
            results.append(result)
            log.info(
                "auto_trader.submitted",
                symbol=signal.symbol,
                strategy=signal.strategy,
                qty=proposal.qty,
                fill_price=order.avg_fill_price,
                order_status=order.status.value,
                session=session_info.session.value,
            )

        except Exception as exc:
            log.error("auto_trader.error", symbol=signal.symbol, error=str(exc))
            results.append(AutoTradeResult(
                signal_id=signal.id or "",
                symbol=signal.symbol,
                approved=False,
                reasons=[f"internal_error: {exc}"],
            ))

    submitted = [r for r in results if r.approved]
    filled = [r for r in submitted if r.order_status == OrderStatus.FILLED.value]
    log.info(
        "auto_trader.batch_done",
        total=len(signals),
        submitted=len(submitted),
        filled=len(filled),
        rejected=len(results) - len(submitted),
    )
    return results


async def run_auto_trading_cycle(universe: list[str] | None = None) -> dict:
    """
    Full autonomous trading cycle:
      1. Check if auto-trading is enabled
      2. Scan for fresh signals
      3. Auto-execute approved ones
    Called by the scheduler continuously.  It scans and submits entries only
    during sessions that can actually trade the requested asset class.
    """
    if not await is_auto_trading_enabled():
        log.debug("auto_trader.disabled_skip")
        return {"skipped": True, "reason": "auto_trading_disabled"}

    session_info = classify_us_equity_session()
    if not _session_enabled(session_info):
        log.debug(
            "auto_trader.session_skip",
            session=session_info.session.value,
            phase=session_info.phase.value,
        )
        return {
            "skipped": True,
            "reason": f"session_disabled:{session_info.session.value}",
            "session": session_info.session.value,
            "phase": session_info.phase.value,
        }

    log.info(
        "auto_trader.cycle_start",
        session=session_info.session.value,
        phase=session_info.phase.value,
    )
    signals = await SignalService().scan(universe, session_info=session_info)
    if not signals:
        return {
            "skipped": False,
            "signals": 0,
            "submitted": 0,
            "filled": 0,
            "session": session_info.session.value,
            "phase": session_info.phase.value,
        }

    results = await auto_execute_signals(signals, session_info=session_info)
    submitted = [r for r in results if r.approved]
    filled = [r for r in submitted if r.order_status == OrderStatus.FILLED.value]
    return {
        "skipped": False,
        "signals": len(signals),
        "submitted": len(submitted),
        "filled": len(filled),
        "executed": len(filled),
        "session": session_info.session.value,
        "phase": session_info.phase.value,
        "results": [
            {
                "symbol": r.symbol,
                "approved": r.approved,
                "reasons": r.reasons,
                "order_id": r.order_id,
                "fill_price": r.fill_price,
                "order_status": r.order_status,
            }
            for r in results
        ],
    }


def _session_enabled(info: MarketSessionInfo) -> bool:
    settings = get_settings()
    if info.session == MarketSession.CLOSED:
        return False
    if info.session == MarketSession.REGULAR:
        return settings.auto_trade_regular_hours
    if info.session == MarketSession.PREMARKET:
        return settings.auto_trade_premarket
    if info.session == MarketSession.AFTERHOURS:
        return settings.auto_trade_afterhours
    if info.session == MarketSession.OVERNIGHT:
        return settings.auto_trade_overnight
    return False
