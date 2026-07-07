# Tradebot

A modular trading platform for stocks and options. Built for research, paper trading,
and Alpaca live routing behind multiple safety gates.

**Status: Phase 2.** Backend, paper/Alpaca brokers, mock + Alpaca data providers,
session-aware stock/options strategies, full risk manager, web dashboard, Docker
setup, and an always-on macOS service option. Alpaca live routing is implemented
behind safety gates. IBKR adapter is still a stub.

---

## What's in here

```
backend/    FastAPI app, SQLAlchemy models, strategies, risk manager,
            paper broker, mock + Alpaca data providers, APScheduler worker,
            Alembic migrations, pytest suite
frontend/   Next.js 14 + TypeScript + Tailwind. Dashboard, signals,
            options scanner, portfolio, paper trading, risk, strategies, settings
infra/      docker-compose.yml and Dockerfiles
```

## Safety model

This codebase will not place a real order until **all** of these are true:

1. `LIVE_TRADING_ENABLED=true` in `.env`
2. `LIVE_TRADING_UNLOCKED=true` at runtime (separate flag, intentional)
3. `TRADING_MODE` is `semi_auto` or `auto`
4. The selected broker uses real capital, for example `BROKER=alpaca` with
   `ALPACA_TRADING_PAPER=false`

All four are checked in one place: `Settings.can_trade_live`. The broker factory
falls back to the local paper broker if a live broker is requested while any check
fails. The IBKR adapter is a deliberate stub that raises `NotYetEnabledError` - it
gets wired up in Phase 4.

Autonomous live entries also require `risk_settings.auto_trading_enabled=true` in
the database and an inactive kill switch. On startup, live-capable mode disables
any inherited paper-mode auto-trading state so a restart cannot silently begin
autonomous live entries. Manual `/api/v1/orders` submissions are risk-checked and
journaled before the broker sees them.

On top of that, every order — paper or live — passes through `RiskManager`, which
enforces:

- Max daily/weekly loss, max per-trade loss
- Max open positions, max trades per day
- Max option premium per contract
- Max ticker concentration where configured; exact duplicate contracts/orders are blocked
- Cooldown after consecutive losses
- Kill switch (UI-toggleable; nothing trades while active)
- Stale-data and wide-spread refusals
- Duplicate-trade prevention

Risk limits live in the database and are editable in the UI at `/risk`. For
non-live automation, stale high DB/UI values are capped to the aggressive
options-paper profile: `$1,500` daily loss, `$500` per-trade risk, `10` open
positions, `30` trades/day, and `$500` option premium. Actual orders still need
enough buying power and must pass spread, confidence, duplicate, and session
checks; live mode uses the explicit persisted limits instead.

## Always-on runtime

The backend owns the scheduler.  To keep scans and exits running after the UI or
Codex app is closed on macOS:

```bash
make service-install
```

See [`ALWAYS_ON.md`](ALWAYS_ON.md) for service status, uninstall, logs, and the
regular/premarket/after-hours/overnight behavior.

## Quick start — Docker

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
cd infra && docker compose up --build
```

Then open:
- `http://localhost:3000` — Next.js dashboard
- `http://localhost:8000/docs` — FastAPI Swagger UI
- `http://localhost:8000/health` — health check

The default config uses the mock data provider, so the app comes up immediately with
synthetic but realistic data. Switch to Alpaca by setting `DATA_PROVIDER=alpaca` and
filling in `ALPACA_API_KEY` / `ALPACA_API_SECRET`. For the step-by-step version, see
[`ALPACA_SETUP.md`](ALPACA_SETUP.md).

## Quick start — local Python + Node

Backend:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# In dev, tables are created automatically on startup.
# In staging/prod, run: alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

## Running tests

```bash
cd backend
pip install -e ".[dev]"
pytest -v
```

The test suite covers:
- **Risk manager** — every gate, every rejection reason, position sizing math
- **Strategies** — momentum breakout, mean reversion, regime detection on synthetic bars
- **Options scanner** — filtering by OI, volume, spread, DTE; liquidity ranking
- **Broker factory** — paper fallback when live gates are closed

Tests run against an in-memory SQLite by default (configured in `tests/conftest.py`),
so no Postgres is needed for unit testing.

## How to switch providers and brokers

Edit `backend/.env`:

| Setting | Options | Effect |
| --- | --- | --- |
| `DATA_PROVIDER` | `mock` / `yahoo` / `alpaca` | Source of quotes, bars, options chains |
| `BROKER` | `paper` / `alpaca` / `ibkr` | Execution venue. `paper` is local simulation; `alpaca` routes to Alpaca paper when `ALPACA_TRADING_PAPER=true` and Alpaca live when the live gates pass; IBKR is Phase 4 |
| `TRADING_MODE` | `research` / `paper` / `copy` / `semi_auto` / `auto` | UI/automation level |

Useful status endpoints:
- `http://localhost:8000/health` - backend liveness
- `http://localhost:8000/market/status` - market open/closed, next open/close, broker/provider
- `http://localhost:8000/integrations/alpaca/status` - Alpaca key/account/clock diagnostics

The provider and broker are swapped at runtime via factories — no other code changes.

## Architecture (the short version)

```
Frontend (Next.js) ──HTTP / WS──► FastAPI
                                    │
                ┌───────────────────┼────────────────────┐
                ▼                   ▼                    ▼
        SignalService          RiskManager        PortfolioService
                │                   │                    │
                ▼                   ▼                    ▼
        Strategy registry    (stateless rules)    BrokerAdapter
                │                                        │
                ▼                                        ▼
        MarketDataProvider              PaperBroker  /  IBKRBroker (stub)
        (Mock / Alpaca)                       │              │
                                              ▼              ▼
                                        PaperEngine     ib_insync (Phase 4)
                                              │
                                              ▼
                                    PostgreSQL · Redis
```

- **MarketDataProvider** is an interface; `MockMarketDataProvider` and `AlpacaMarketDataProvider`
  implement it. The rest of the system never imports a concrete provider.
- **BrokerAdapter** is the same pattern. `PaperBroker` is fully functional; `IBKRBroker`
  raises until Phase 4.
- **Strategy** is a pure class. Bars/quotes go in, `Signal` objects come out. No I/O.
- **RiskManager** is stateless — caller passes state and config in, gets `RiskDecision` out.
- **PaperEngine** owns persistent paper state (cash, positions, fills) in Postgres.
- **APScheduler** runs inside the FastAPI process by default. You can split it out to a
  separate `worker` service later — the compose file has the profile configured.

## Roadmap

- **Phase 1** ✅ Backend foundation, paper broker, three strategies, full UI scaffold
- **Phase 2** — Real Alpaca streaming, options scanner depth, signal approval UI
- **Phase 3** — Backtesting engine, trade journal, PWA support, notifications
- **Phase 4** — IBKR adapter (paper account first)
- **Phase 5** — Live trading UX hardening, monitoring, prod deploy
- **Phase 6** — AI explainer, news/earnings, mobile (Capacitor) wrappers

## Disclaimer

This project is for research and education. Markets are adversarial; backtests lie;
paper performance overstates live performance; bugs in trading systems lose real money.
Nobody — not the author, not the model that helped write the code — is responsible
for what happens if you run this with real capital. Read every line you depend on.
# TradePilot
