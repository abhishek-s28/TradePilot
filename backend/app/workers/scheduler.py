"""Background worker.

Runs scheduled jobs:
 - Signal scan every N minutes (during market hours)
 - Position monitor every minute (enforce stops/targets in paper)
 - Daily summary at close

Keep this idempotent and safe to crash/restart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.auto_trader import run_auto_trading_cycle
from app.services.position_monitor import run_position_monitor

log = get_logger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def job_signal_scan() -> None:
    """Every 5 min: scan for signals + auto-execute if enabled."""
    try:
        result = await run_auto_trading_cycle()
        if result.get("skipped"):
            log.debug("auto_cycle.skipped", reason=result.get("reason"))
        else:
            log.info(
                "auto_cycle.done",
                signals=result.get("signals", 0),
                executed=result.get("executed", 0),
            )
    except Exception as exc:  # noqa: BLE001
        log.error("auto_cycle.failed", error=str(exc))


async def job_position_monitor() -> None:
    """Check stop-loss / take-profit on open positions."""
    try:
        result = await run_position_monitor()
        if result.get("exited"):
            log.info(
                "position_monitor.done",
                checked=result.get("checked"),
                exited=result.get("exited"),
                session=result.get("session"),
            )
    except Exception as exc:  # noqa: BLE001
        log.error("position_monitor.failed", error=str(exc))


def start_scheduler() -> None:
    if scheduler.running:
        return
    from datetime import timedelta
    settings = get_settings()
    # Scan every 60 seconds; first run fires ~15 s after startup so bootstrap completes first.
    scheduler.add_job(
        job_signal_scan, "interval", seconds=settings.auto_trade_scan_interval_seconds,
        id="signal_scan",
        coalesce=True, max_instances=1,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
    )
    scheduler.add_job(
        job_position_monitor, "interval", seconds=settings.auto_trade_exit_interval_seconds,
        id="position_monitor",
        coalesce=True, max_instances=1,
    )
    scheduler.start()
    log.info("scheduler.started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")


# Allow running as a standalone worker process too
async def _main() -> None:
    from app.core.logging import configure_logging
    from app.core.settings import get_settings
    from app.database.session import Base, get_engine
    import app.database.models  # noqa: F401  -- register tables on metadata

    configure_logging()

    # Dev convenience parity with the API: create tables if they don't exist.
    # Production runs Alembic and sets APP_ENV=production.
    if get_settings().app_env == "development":
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    start_scheduler()
    log.info("worker.up")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        stop_scheduler()


if __name__ == "__main__":
    asyncio.run(_main())
