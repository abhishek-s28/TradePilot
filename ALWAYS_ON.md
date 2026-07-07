# Always-On Trading Runtime

The backend owns the scheduler.  The frontend can be closed; trading continues
as long as the backend process is alive.

## Install macOS Background Service

```bash
cd /Users/shah/Downloads/tradebot
make service-install
```

This installs a LaunchAgent named `com.tradebot.backend` that starts the API at
`http://127.0.0.1:8000`, restarts it if it exits, and runs it again after login.

Useful commands:

```bash
make service-status
make service-uninstall
tail -f ~/Library/Logs/tradebot/backend.out.log
tail -f ~/Library/Logs/tradebot/backend.err.log
```

> **Note:** Log files are written to `~/Library/Logs/tradebot/` (not `logs/` inside the
> project) because macOS LaunchAgent security prevents log writes to paths under
> `~/Downloads/`. If you reinstall the service this is handled automatically.

### Preventing sleep

The service restarts automatically if it crashes, but if the Mac sleeps the
scheduler pauses. To keep it awake while trading:

```bash
# Keep awake during market hours (9 AM – 4:30 PM ET on weekdays)
caffeinate -i &
```

Or configure a permanent wake schedule via System Settings → Battery → Schedule.

## Session Behavior

- Regular hours: stocks and options can be scanned and submitted.
- Premarket, after-hours, and overnight entries are disabled by default. Stock/ETF
  limit orders can use extended-hours routing only if those session flags are
  explicitly enabled.
- Options are kept regular-hours only because Alpaca rejects option orders with
  `extended_hours=true`.
- The exit monitor runs separately from entries and checks stop-loss/take-profit
  levels every `AUTO_TRADE_EXIT_INTERVAL_SECONDS`.

## Safety Switches

The DB kill switch still overrides everything.  Live trading remains blocked
unless `LIVE_TRADING_ENABLED=true`, `LIVE_TRADING_UNLOCKED=true`, auto/semi-auto
mode is selected, and the broker is configured for live routing.

When those live gates are open, backend startup disables inherited
`auto_trading_enabled=true` state from paper mode. Re-enable autonomous entries
from the UI or `POST /auto-trading/enable` only after confirming the live broker,
market status, risk settings, and kill switch state.
