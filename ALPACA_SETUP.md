# Alpaca Setup Runbook

This is the safe path for connecting Alpaca. Start with Alpaca paper trading,
prove the bot over time, then move to live routing only after review.

## Codex Prompt

Use this prompt when you want Codex to configure or audit the Alpaca connection:

```text
You are working in /Users/shah/Downloads/tradebot. Configure the bot for Alpaca the professional way.

Goals:
- Never hard-code API keys or print secrets.
- Read ALPACA_API_KEY and ALPACA_API_SECRET only from backend/.env or the process environment.
- Use Alpaca paper trading first. Keep LIVE_TRADING_ENABLED=false, LIVE_TRADING_UNLOCKED=false, TRADING_MODE=paper, and ALPACA_TRADING_PAPER=true unless I explicitly ask for a reviewed live-trading change.
- Set DATA_PROVIDER=alpaca only after keys are present.
- Use BROKER=alpaca only for Alpaca paper order routing; otherwise keep BROKER=paper for local simulated fills.
- Prove the connection by checking /health, /market/status, and /integrations/alpaca/status.
- Confirm the UI shows market open/closed, next open/close, data provider, broker, kill switch, and auto-trading state.
- Run the backend tests before declaring the setup complete.

Do not promise profit. Focus on reliable infrastructure, clean risk controls, auditability, and paper-trading validation.
```

## What To Put In `backend/.env`

Paste your Alpaca paper key and secret locally. Do not paste them into chat.

```dotenv
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
LIVE_TRADING_UNLOCKED=false

DATA_PROVIDER=alpaca
ALPACA_API_KEY=your_paper_key_here
ALPACA_API_SECRET=your_paper_secret_here
ALPACA_DATA_FEED=iex

# Local simulated broker:
BROKER=paper

# Or Alpaca paper order routing:
# BROKER=alpaca
ALPACA_TRADING_PAPER=true
```

Use `BROKER=paper` when you want local simulated fills stored in `tradebot.db`.
Use `BROKER=alpaca` when you want paper orders sent to Alpaca's paper account.

## Live Alpaca Routing

Live routing is implemented for Alpaca through the same broker adapter. Use live
Alpaca keys locally in `backend/.env` and flip only the non-secret switches:

```dotenv
TRADING_MODE=auto
LIVE_TRADING_ENABLED=true
LIVE_TRADING_UNLOCKED=true

DATA_PROVIDER=alpaca
BROKER=alpaca
ALPACA_TRADING_PAPER=false
```

On startup, the backend connects to Alpaca's live trading endpoint only when all
live gates pass. Manual orders submitted through `/api/v1/orders` are risk-checked
and journaled before routing to Alpaca. Autonomous live entries require the DB
auto-trading toggle to be enabled while the live process is running; startup
disables any inherited paper-mode `auto_trading_enabled=true` state.

If live startup fails with `401 Unauthorized` from `https://api.alpaca.markets`,
the key pair is not authorized for Alpaca live trading. Restore
`ALPACA_TRADING_PAPER=true` or replace the local key and secret with live Alpaca
credentials, then restart the backend.

You can test both environments without changing routing:

```bash
curl 'http://localhost:8000/integrations/alpaca/status?paper=true'
curl 'http://localhost:8000/integrations/alpaca/status?paper=false'
```

Use the kill switch before making config changes:

```bash
curl -X POST 'http://localhost:8000/risk/kill-switch?active=true'
```

## Run Locally

Backend:

```bash
cd /Users/shah/Downloads/tradebot/backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd /Users/shah/Downloads/tradebot/frontend
npm run dev
```

Open:

- Frontend: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`
- Market status: `http://localhost:8000/market/status`
- Alpaca status: `http://localhost:8000/integrations/alpaca/status`

## How To Know The Server And Market Are Open

- Server is open if `GET /health` returns `{"status":"ok"}`.
- Market state is in `GET /market/status`.
- Alpaca key/account/clock diagnostics are in `GET /integrations/alpaca/status`.
- The app sidebar also shows market open/closed, broker, and auto-trading state.

## 24/7 Reality Check

Running 24/7 means the process stays alive 24/7. It does not mean stocks/options
trade 24/7. The scheduler wakes up continuously: regular hours can trade stocks
and options; premarket, after-hours, and overnight sessions can submit stock/ETF
limit orders with extended-hours routing when the broker supports it. Options are
kept regular-hours only. Stops and targets are monitored by a separate exit loop.

For a personal Mac, install the LaunchAgent:

```bash
cd /Users/shah/Downloads/tradebot
make service-install
```

For production-grade 24/7, use Docker/cloud hosting with restart policies, logs,
backups, and alerts.
