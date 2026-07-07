"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";
import type { MarketStatus } from "@/types/api";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

export default function SettingsPage() {
  const status = useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<MarketStatus>("/market/status"),
    refetchInterval: 10_000,
  });

  const s = status.data;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Settings & System</h1>

      <Card title="Runtime configuration">
        {!s ? (
          <div className="text-sm text-muted">Loading…</div>
        ) : (
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <Row label="Trading mode" value={<Pill>{s.trading_mode}</Pill>} />
            <Row
              label="Data provider"
              value={
                <span className="flex items-center gap-2">
                  <Pill>{s.data_provider}</Pill>
                  {s.data_provider === "mock" && (
                    <span className="text-xs text-yellow-400">
                      (synthetic data — for development)
                    </span>
                  )}
                </span>
              }
            />
            <Row
              label="Broker"
              value={
                <span className="flex items-center gap-2">
                  <Pill tone={s.broker_connected ? "bull" : "warn"}>{s.broker}</Pill>
                  <span className="text-xs text-muted">
                    {s.broker_connected ? "connected" : "not connected"}
                  </span>
                </span>
              }
            />
            <Row
              label="Regular market"
              value={
                <Pill tone={s.market_open ? "bull" : "default"}>
                  {s.market_open ? "open" : "closed"}
                </Pill>
              }
            />
            <Row
              label="Auto-trade session"
              value={
                <span className="flex items-center gap-2">
                  <Pill tone={s.equity_session_open ? "bull" : "default"}>
                    {s.market_clock.session ?? "closed"}
                  </Pill>
                  <span className="text-xs text-muted">{s.market_clock.phase}</span>
                </span>
              }
            />
            <Row
              label={s.market_open ? "Next close" : "Next open"}
              value={
                <span className="font-mono text-xs">
                  {formatMaybeDate(
                    s.market_open
                      ? s.market_clock.next_close
                      : s.market_clock.next_open,
                  )}
                </span>
              }
            />
            <Row
              label="Live trading enabled (.env)"
              value={
                <span className="flex items-center gap-2">
                  {s.live_trading_enabled ? (
                    <CheckCircle2 size={14} className="text-bear" />
                  ) : (
                    <AlertTriangle size={14} className="text-bull" />
                  )}
                  <code className="font-mono text-xs">{String(s.live_trading_enabled)}</code>
                </span>
              }
            />
            <Row
              label="Live trading unlocked (runtime)"
              value={
                <span className="flex items-center gap-2">
                  {s.live_trading_unlocked ? (
                    <CheckCircle2 size={14} className="text-bear" />
                  ) : (
                    <AlertTriangle size={14} className="text-bull" />
                  )}
                  <code className="font-mono text-xs">{String(s.live_trading_unlocked)}</code>
                </span>
              }
            />
            <Row
              label="Can trade live"
              value={
                <Pill tone={s.can_trade_live ? "bear" : "bull"}>
                  {s.can_trade_live ? "YES — LIVE ORDERS ARE POSSIBLE" : "No — paper only"}
                </Pill>
              }
            />
            <Row
              label="Server time"
              value={
                <span className="font-mono text-xs">{new Date(s.time).toLocaleString()}</span>
              }
            />
          </dl>
        )}
      </Card>

      {s && s.trading_mode !== "paper" ? (
        <Card title="Trading mode notice">
          <div className="flex items-start gap-3 rounded-md border border-yellow-400/30 bg-yellow-500/10 p-4 text-sm text-yellow-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0" />
            <div>
              <p>
                Trading mode is set to <strong>{s.trading_mode}</strong>. Live orders remain disabled
                unless <code className="font-mono text-xs">LIVE_TRADING_ENABLED=true</code>,{' '}
                <code className="font-mono text-xs">LIVE_TRADING_UNLOCKED=true</code>, and the broker is not
                <code className="font-mono text-xs"> paper</code>.
              </p>
              <p className="mt-2 text-xs text-muted">
                Keep live trading disabled in local development by staying on <strong>paper</strong> mode.
              </p>
            </div>
          </div>
        </Card>
      ) : null}

      <Card title="How to change these">
        <ul className="list-disc pl-5 text-sm text-muted space-y-2">
          <li>
            <code className="font-mono text-xs">TRADING_MODE</code>,{" "}
            <code className="font-mono text-xs">DATA_PROVIDER</code>,{" "}
            <code className="font-mono text-xs">BROKER</code> are set in the backend{" "}
            <code className="font-mono text-xs">.env</code> file. Restart the backend after
            editing.
          </li>
          <li>
            Live trading needs BOTH{" "}
            <code className="font-mono text-xs">LIVE_TRADING_ENABLED=true</code> in env AND{" "}
            <code className="font-mono text-xs">LIVE_TRADING_UNLOCKED=true</code> at runtime.
            The unlock flow is intentionally not exposed in the UI yet — it lands in Phase 5.
          </li>
          <li>
            Risk limits — daily loss, position size, etc. — live in the database and are
            editable on the <code className="font-mono text-xs">/risk</code> page.
          </li>
        </ul>
      </Card>

      <Card>
        <div className="flex items-start gap-3 text-sm text-muted">
          <AlertTriangle size={16} className="text-yellow-400 shrink-0 mt-0.5" />
          <p>
            This is Phase 1. Mock data is synthetic — useful for development but obviously not
            tradeable. Switch to <code className="font-mono text-xs">DATA_PROVIDER=alpaca</code>{" "}
            with your Alpaca keys to get real market data, still paper-traded.
          </p>
        </div>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/50 last:border-0 pb-2 last:pb-0">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}

function formatMaybeDate(value: string | null) {
  if (!value) return "unknown";
  return new Date(value).toLocaleString();
}
