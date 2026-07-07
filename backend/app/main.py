"""FastAPI application."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.v1 import analytics, auto_trading, health, market, news, options, portfolio, research, risk, signals, strategies, trading, universe, ws
from app.auto_trade.loop import AutoTradeLoop
from app.auto_trade.watchlist import derived_watchlist, universe_count
from app.brokers.factory import get_broker, shutdown_broker
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.data.factory import get_provider, shutdown_provider
from app.database.models import (  # noqa: F401  -- ensure tables registered
    AuditLog,
    OrderRow,
    PaperAccount,
    PositionRow,
    RiskSettings,
    SignalRow,
    StrategyConfig,
    SystemEvent,
    User,
    Watchlist,
)
from app.database.session import Base, dispose_engine, get_engine, session_factory

configure_logging()
log = get_logger(__name__)
auto_loop: AutoTradeLoop | None = None


async def _bootstrap_settings() -> None:
    """Ensure RiskSettings exists without silently unlocking live automation."""
    settings = get_settings()
    default_auto_enabled = False
    async with session_factory()() as s:
        res = await s.execute(select(RiskSettings).limit(1))
        row = res.scalar_one_or_none()
        if row is None:
            row = RiskSettings(
                max_daily_loss_usd=settings.risk_max_daily_loss_usd,
                max_trade_loss_usd=settings.risk_max_trade_loss_usd,
                max_open_positions=settings.risk_max_open_positions,
                max_trades_per_day=settings.risk_max_trades_per_day,
                max_option_premium_usd=settings.risk_max_option_premium_usd,
                cooldown_after_losses=settings.risk_cooldown_after_losses,
                auto_trading_enabled=default_auto_enabled,
                kill_switch_active=False,
                allowed_strategies=[],
                allowed_tickers=[],
            )
            s.add(row)
            log.info(
                "bootstrap.risk_settings_created",
                auto_trading_enabled=default_auto_enabled,
                can_trade_live=settings.can_trade_live,
            )
        else:
            row.max_daily_loss_usd = settings.risk_max_daily_loss_usd
            row.max_trade_loss_usd = settings.risk_max_trade_loss_usd
            row.max_open_positions = settings.risk_max_open_positions
            row.max_trades_per_day = settings.risk_max_trades_per_day
            row.max_option_premium_usd = settings.risk_max_option_premium_usd
            row.cooldown_after_losses = settings.risk_cooldown_after_losses
            if settings.can_trade_live:
                if row.auto_trading_enabled:
                    log.warning("bootstrap.live_auto_trading_disabled_on_startup")
                row.auto_trading_enabled = False
            else:
                row.auto_trading_enabled = bool(row.auto_trading_enabled and not row.kill_switch_active)
            log.info(
                "bootstrap.risk_settings_updated",
                auto_trading_enabled=row.auto_trading_enabled,
                kill_switch_active=row.kill_switch_active,
                can_trade_live=settings.can_trade_live,
            )
        await s.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global auto_loop
    s = get_settings()
    log.info("app.startup", env=s.app_env, mode=s.trading_mode.value, broker=s.broker)
    if s.broker == "alpaca" or s.data_provider == "alpaca":
        s.validate_alpaca_credentials()

    # Create tables in dev only. Staging/prod MUST run Alembic migrations:
    #   cd backend && alembic upgrade head
    if s.app_env == "development":
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("app.tables_ensured_via_create_all")

    # Initialize provider & broker (pre-warms singletons)
    await get_provider()
    broker = await get_broker()
    account = await broker.get_account()
    print(
        "Alpaca connected | "
        f"equity=${account.equity:,.2f} | "
        f"buying_power=${account.buying_power:,.2f} | "
        f"open_positions={account.open_positions} | "
        f"today_pnl=${account.daily_pnl:,.2f}"
    )

    # Ensure risk settings exist before the background loop starts.
    await _bootstrap_settings()

    count = await universe_count()
    watchlist = await derived_watchlist()
    auto_loop = AutoTradeLoop()
    await auto_loop.start()
    print(
        "AutoTradeLoop started | "
        "options_strategies=8 | "
        f"universe={count} tradable | "
        f"watchlist={len(watchlist)} derived | "
        f"scan={s.auto_trade_scan_interval_seconds}s"
    )

    yield

    if auto_loop:
        await auto_loop.stop()
    await shutdown_broker()
    await shutdown_provider()
    await dispose_engine()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Tradebot API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — locked to frontend in production
    cors_origins = (
        ["*"] if s.app_env == "development" else ["https://your-domain.example"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(health.router)
    app.include_router(market.router,   prefix="/api/v1")
    app.include_router(trading.router,  prefix="/api/v1")
    app.include_router(signals.router)
    app.include_router(options.router)
    app.include_router(portfolio.router)
    app.include_router(risk.router)
    app.include_router(strategies.router)
    app.include_router(universe.router)
    app.include_router(auto_trading.router)
    app.include_router(analytics.router)
    app.include_router(research.router)
    app.include_router(research.router, prefix="/api/v1")
    app.include_router(news.router,    prefix="/api/v1")
    app.include_router(ws.router)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": "Tradebot API",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
            "frontend": "http://localhost:3000",
        }

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # noqa: ARG001
        log.exception("unhandled.error", path=request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    return app


app = create_app()


async def _self_check() -> None:
    s = get_settings()
    if s.broker == "alpaca" or s.data_provider == "alpaca":
        s.validate_alpaca_credentials()
    broker = await get_broker()
    account = await broker.get_account()
    print(
        "Alpaca connected | "
        f"equity=${account.equity:,.2f} | "
        f"buying_power=${account.buying_power:,.2f} | "
        f"open_positions={account.open_positions} | "
        f"today_pnl=${account.daily_pnl:,.2f}"
    )
    count = await universe_count()
    watchlist = await derived_watchlist()
    print(
        "AutoTradeLoop started | "
        "options_strategies=8 | "
        f"universe={count} tradable | "
        f"watchlist={len(watchlist)} derived | "
        f"scan={s.auto_trade_scan_interval_seconds}s"
    )
    await shutdown_broker()


if __name__ == "__main__":
    asyncio.run(_self_check())
