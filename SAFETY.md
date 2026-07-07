# Safety Model

## Default safe settings
The project is configured to stay in paper trading mode by default:
- `TRADING_MODE=paper`
- `LIVE_TRADING_ENABLED=false`
- `LIVE_TRADING_UNLOCKED=false`
- `BROKER=paper`
- `DATA_PROVIDER=mock`
- `ALPACA_TRADING_PAPER=true`

## Live trading gating
Live trading can only occur when all of the following are true:
1. `LIVE_TRADING_ENABLED=true`
2. `LIVE_TRADING_UNLOCKED=true`
3. `TRADING_MODE` is `semi_auto` or `auto`
4. The selected broker uses real capital, for example `BROKER=alpaca` with
   `ALPACA_TRADING_PAPER=false`

The single source of truth is `Settings.can_trade_live` in `backend/app/core/settings.py`.
Autonomous entries also require `risk_settings.auto_trading_enabled=true` in the
database and `kill_switch_active=false`. When the backend starts in live-capable
mode, it disables inherited auto-trading state so paper-mode automation cannot
silently become live automation after a restart.

## Risk limits enforced by default
The backend enforces these limits on every order, including paper trades:
- `RISK_MAX_DAILY_LOSS_USD`
- `RISK_MAX_TRADE_LOSS_USD`
- `RISK_MAX_OPEN_POSITIONS`
- `RISK_MAX_TRADES_PER_DAY`
- `RISK_MAX_OPTION_PREMIUM_USD`
- `RISK_COOLDOWN_AFTER_LOSSES`

These are set in `backend/.env.example` and overridable in the DB via the risk UI.
Manual orders submitted through `/api/v1/orders` also pass through this risk
layer before reaching Alpaca and are journaled locally for auditability.

When live trading is not possible, the loader applies conservative caps so stale
DB/UI settings cannot make the options bot overtrade:
- daily loss cap: `$1,500`
- per-trade risk cap: `$500`
- max open positions cap: `10`
- max trades per day cap: `30`
- option premium cap: `$500`

For small paper accounts, the dollar caps also cannot exceed account equity, and
actual orders still require enough buying power. Live mode does not inherit
these paper-only scaling rules.

These caps are not applied when live trading is possible; live mode uses the
explicit persisted limits.

## Local dev safety
- `backend/.env` uses `sqlite+aiosqlite:///./tradebot.db` so local development does not require Postgres.
- `frontend/.env.local` points the UI at `http://localhost:8000`.
- No real API keys or secrets are stored in local env files.

## UI warnings
The frontend settings page shows live trading state, unlock state, and whether live orders are possible.
If a non-paper trading mode is selected, it warns the user before live trading is enabled.

## Notes
- Do not set `LIVE_TRADING_ENABLED=true` unless you are intentionally enabling a live mode.
- Do not set `LIVE_TRADING_UNLOCKED=true` in normal development.
- Keep `BROKER=paper` and `DATA_PROVIDER=mock` for fully local safe runs.
- Use `BROKER=alpaca` only with `ALPACA_TRADING_PAPER=true` until paper trading
  has been reviewed over time.
- Use the kill switch before changing live broker credentials or restarting a
  live-capable backend.
