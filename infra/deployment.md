# Deployment notes

What it takes to run this safely beyond `docker compose up`. This document is the gap
list between "runs locally" and "runs in production." Phase 1 ships the code; closing
this gap is largely Phase 5.

## Before you deploy anywhere

1. **Generate a real `SECRET_KEY`.** `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
   Never commit it. Set it via the secret manager of whatever platform you're on (Doppler,
   AWS Secrets Manager, GCP Secret Manager, Fly secrets, Railway env, etc).
2. **Lock CORS.** `backend/app/main.py` uses `["*"]` in development. In production it
   reads from a list — set the real domain.
3. **Lock the database.** Don't expose Postgres or Redis to the internet. Bind to the
   container network only; in compose, remove the `ports:` entries from `db` and `redis`.
4. **TLS.** Put the API and frontend behind a reverse proxy with a real certificate
   (Caddy, Nginx, Traefik, or a managed LB). The API is HTTP-only by itself.
5. **Run Alembic, not `create_all()`.** Production deploys must run `alembic upgrade head`
   before the API starts. `app.main` only auto-creates tables when `APP_ENV=development`.

## Secret management

The repo has `.env.example` files and a `.dockerignore` that excludes `.env`. Don't
push real secrets through env files in a CI pipeline — fetch them from your secret store
at deploy time and inject as env vars on the container.

Secrets that exist in this codebase:
- `SECRET_KEY` (JWT signing, future)
- `ALPACA_API_KEY`, `ALPACA_API_SECRET`
- `IBKR_ACCOUNT` (when Phase 4 lands)
- `DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN` (Phase 3)
- The Postgres password

## Live trading enablement

There is **no UI toggle** for `LIVE_TRADING_ENABLED` and `LIVE_TRADING_UNLOCKED`. This
is by design. Enabling live trading should require:

1. A code or config change (changing `.env` and restarting), reviewed by a second person.
2. Proof that paper has run profitably for N consecutive days.
3. A separate runtime "unlock" step (Phase 5 will land this as a CLI command requiring
   a confirmation token).

If you find yourself wanting to skip any of these, stop and reread `app/risk/manager.py`.

## Monitoring hooks (Phase 5 wires these)

The application emits structured JSON logs through `structlog`. To wire up monitoring:

- **Sentry**: install `sentry-sdk[fastapi]`, call `sentry_sdk.init(dsn=...)` in `main.py`
  before `create_app()`. The unhandled-exception handler already exists.
- **Prometheus**: add `prometheus-fastapi-instrumentator`, expose `/metrics`, and scrape
  it. Key custom metrics to add: `signals_generated_total`, `orders_placed_total`,
  `orders_rejected_total{reason}`, `risk_kill_switch_active`, `paper_realized_pnl`,
  `broker_connected`.
- **Logs**: ship `stdout` JSON to whatever you use (Datadog, Loki, CloudWatch). The log
  schema is consistent because every log call goes through `structlog`.

## Production checklist

- [ ] `APP_ENV=production` in environment
- [ ] `SECRET_KEY` generated and stored in secret manager
- [ ] Real database with backups; daily snapshots verified to restore
- [ ] Real Redis (or stripped — we don't lean on it heavily yet)
- [ ] CORS origins set to the actual frontend domain
- [ ] TLS terminated upstream; HTTP redirects to HTTPS
- [ ] Database and Redis not internet-exposed
- [ ] `alembic upgrade head` runs as a deploy step before the API starts
- [ ] `LIVE_TRADING_ENABLED=false` until paper has proven out
- [ ] Sentry DSN configured
- [ ] Log aggregation pointed at the right place
- [ ] Alerting set up for: API down, scheduler down, kill-switch toggled, broker
      disconnected, daily loss approaching limit
- [ ] Container image scanned for CVEs (trivy, grype, your registry's scanner)
- [ ] Non-root user in containers (already configured in our Dockerfiles)
- [ ] Healthcheck endpoints used by the orchestrator
- [ ] Restart policies are `unless-stopped` or platform-managed restart
- [ ] On-call rotation for off-hours; a runbook for "the bot is doing something
      it shouldn't" — primary tool: kill switch via API

## Scaling

The single-process backend with embedded APScheduler is fine for one user. If you
fan out to multiple replicas, the embedded scheduler becomes a problem: each replica
will fire its own scans. Options:

1. **Split worker out**: the compose file already has a `worker` profile. Run exactly
   one worker, multiple API replicas. Remove `start_scheduler()` from the API lifespan.
2. **Use a distributed scheduler**: APScheduler has a SQLAlchemyJobStore backend that
   coordinates across processes. Or switch to Celery beat — heavier but battle-tested.

For data: WebSocket fan-out to many clients eventually wants Redis pub/sub instead of
the per-process broadcast in `api/v1/ws.py`. Not urgent for personal use.

## Disaster recovery

The state of record is Postgres. Lose it, lose your paper P&L history and audit log.
Strategies, market data, and code are all reproducible from the repo. So:

- Postgres backed up nightly, retained 30 days
- Test the restore quarterly — an untested backup is a hope, not a backup
- Document the restore procedure in this file before you need it

## Things that will bite you

- **IBKR Gateway / TWS lifecycle**: ib_insync needs a TWS or Gateway running and
  authenticated. When TWS auto-logs out after 24h, your bot loses its broker connection.
  Phase 4 will document the standard workaround (re-login via `IBC` automation) and
  set up a watchdog.
- **Market data feed gaps**: even paid feeds drop. The risk manager checks quote
  staleness already; alert on `quote_stale` rejection rates spiking.
- **Time sync**: your container clock matters for stale-data checks. Run `chrony` or
  whatever the platform provides; don't trust default container clocks indefinitely.
- **Daylight saving / market holidays**: the scheduler runs UTC. Market hours change
  twice a year and there are ~10 holidays. `provider.is_market_open()` is the source
  of truth; check that integration in March and November.
