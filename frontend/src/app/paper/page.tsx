"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Stat, Pill } from "@/components/primitives";
import { formatUSD, cn } from "@/lib/utils";
import type { AccountSnapshot } from "@/types/api";
import { Bot, Loader2, Play, RotateCcw, Square, TrendingDown, TrendingUp, Zap } from "lucide-react";

interface AutoStatus {
  auto_trading_enabled: boolean;
  kill_switch_active: boolean;
  effectively_active: boolean;
  recent_trades: Array<{
    message: string;
    payload: Record<string, unknown>;
    time: string | null;
  }>;
}

interface MarketStatus {
  broker: string;
  data_provider: string;
}

interface ActivityEvent {
  id: string;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
  severity: string;
  time: string | null;
}

export default function PaperPage() {
  const qc = useQueryClient();
  const [cash, setCash] = useState(100_000);
  const [confirming, setConfirming] = useState(false);

  const account = useQuery({
    queryKey: ["broker-account"],
    queryFn: () => api<AccountSnapshot>("/v1/account"),
    refetchInterval: 5_000,
  });

  const market = useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<MarketStatus>("/market/status"),
    refetchInterval: 10_000,
  });

  const autoStatus = useQuery({
    queryKey: ["auto-trading-status"],
    queryFn: () => api<AutoStatus>("/auto-trading/status"),
    refetchInterval: 5_000,
  });

  const activity = useQuery({
    queryKey: ["auto-activity"],
    queryFn: () => api<ActivityEvent[]>("/auto-trading/activity?limit=30"),
    refetchInterval: 5_000,
  });

  const reset = useMutation({
    mutationFn: () =>
      api<{ status: string; starting_cash: number }>(
        `/paper/reset?starting_cash=${cash}`,
        { method: "POST" },
      ),
    onSuccess: () => {
      setConfirming(false);
      qc.invalidateQueries({ queryKey: ["paper-account"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["analytics-stats"] });
      qc.invalidateQueries({ queryKey: ["analytics-journal"] });
      qc.invalidateQueries({ queryKey: ["analytics-equity"] });
    },
  });

  const enableAuto = useMutation({
    mutationFn: () => api("/auto-trading/enable", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auto-trading-status"] }),
  });

  const disableAuto = useMutation({
    mutationFn: () => api("/auto-trading/disable", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auto-trading-status"] }),
  });

  const runNow = useMutation({
    mutationFn: () => api<{ signals: number; executed: number }>("/auto-trading/run-now", { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auto-trading-status"] });
      qc.invalidateQueries({ queryKey: ["auto-activity"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["signals"] });
    },
  });

  const a = account.data;
  const auto = autoStatus.data;
  const active = auto?.effectively_active ?? false;
  const isLocalPaper = market.data?.broker === "paper";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Paper Trading</h1>
        <span className="text-xs text-muted">
          {market.data?.broker === "alpaca_paper"
            ? "Alpaca paper broker · real market data · no real money"
            : "Local simulated account · no real money"}
        </span>
      </div>

      {/* Account stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Equity" value={a ? formatUSD(a.equity) : "—"} />
        <Stat label="Cash" value={a ? formatUSD(a.cash) : "—"} />
        <Stat label="Positions value" value={a ? formatUSD(a.positions_value) : "—"} />
        <Stat label="Open positions" value={a?.open_positions ?? "—"} />
      </div>

      {/* Auto-trading control */}
      <Card title={
        <span className="flex items-center gap-2">
          <Bot size={15} />
          Autonomous Paper Trading
          {active && (
            <span className="flex items-center gap-1 text-xs text-bull ml-2">
              <span className="w-1.5 h-1.5 rounded-full bg-bull animate-pulse" />
              LIVE
            </span>
          )}
        </span>
      }>
        <div className="space-y-4">
          <p className="text-sm text-muted">
            When enabled, the bot scans the market continuously, evaluates all signals
            through the risk engine, and automatically submits approved paper trades to
            the active broker.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            {active ? (
              <button
                onClick={() => disableAuto.mutate()}
                disabled={disableAuto.isPending}
                className="px-4 py-2 rounded bg-bear/20 text-bear border border-bear/30 text-sm flex items-center gap-2 hover:bg-bear/30 disabled:opacity-50"
              >
                {disableAuto.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Square size={14} />
                )}
                Stop auto-trading
              </button>
            ) : (
              <button
                onClick={() => enableAuto.mutate()}
                disabled={enableAuto.isPending || (auto?.kill_switch_active ?? false)}
                className="px-4 py-2 rounded bg-bull/20 text-bull border border-bull/30 text-sm flex items-center gap-2 hover:bg-bull/30 disabled:opacity-50"
              >
                {enableAuto.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}
                Start auto-trading
              </button>
            )}

            <button
              onClick={() => runNow.mutate()}
              disabled={runNow.isPending || !active}
              className="px-3 py-2 rounded bg-accent/20 text-accent border border-accent/30 text-sm flex items-center gap-2 hover:bg-accent/30 disabled:opacity-50"
              title={!active ? "Enable auto-trading first" : "Trigger one scan+trade cycle now"}
            >
              {runNow.isPending ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Zap size={14} />
              )}
              Scan &amp; trade now
            </button>

            <div className="ml-auto text-sm">
              <span className="text-muted">Status: </span>
              <Pill tone={active ? "bull" : "default"}>
                {active ? "Auto-trading active" : auto?.kill_switch_active ? "Kill switch active" : "Manual only"}
              </Pill>
            </div>
          </div>

          {runNow.data && (
            <div className="text-sm rounded border border-border bg-panel2/50 px-3 py-2">
              Scan done — {runNow.data.signals} signal{runNow.data.signals !== 1 ? "s" : ""} found,{" "}
              <span className="text-bull font-medium">{runNow.data.executed} executed</span>.
            </div>
          )}

          {runNow.isError && (
            <div className="text-sm text-bear">
              {(runNow.error as Error)?.message}
            </div>
          )}

          {auto?.kill_switch_active && (
            <div className="text-sm text-bear border border-bear/30 rounded px-3 py-2 bg-bear/10">
              Kill switch is active — go to the Risk page to deactivate it before enabling auto-trading.
            </div>
          )}
        </div>
      </Card>

      {/* Activity feed */}
      <Card title="Auto-trade activity log">
        {(activity.data?.length ?? 0) === 0 ? (
          <div className="text-sm text-muted">
            No auto-trades recorded yet. Enable auto-trading above to start.
          </div>
        ) : (
          <ul className="divide-y divide-border max-h-96 overflow-y-auto">
            {(activity.data ?? []).map((e) => {
              const p = e.payload as any;
              const bull = p?.direction === "bullish";
              return (
                <li key={e.id} className="py-2.5 flex items-start gap-2.5 text-sm">
                  {e.kind === "auto_trade" ? (
                    bull ? (
                      <TrendingUp size={14} className="text-bull mt-0.5 shrink-0" />
                    ) : (
                      <TrendingDown size={14} className="text-bear mt-0.5 shrink-0" />
                    )
                  ) : (
                    <RotateCcw size={14} className="text-muted mt-0.5 shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-sm">{e.message}</div>
                    <div className="text-[10px] text-muted/60 mt-0.5">
                      {e.time ? new Date(e.time).toLocaleString() : ""}
                    </div>
                  </div>
                  <Pill
                    tone={
                      e.kind === "auto_trade"
                        ? "bull"
                        : e.kind === "auto_exit"
                          ? "warn"
                          : "default"
                    }
                  >
                    {e.kind}
                  </Pill>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {/* Reset */}
      <Card title="Reset paper account">
        <p className="text-sm text-muted mb-3">
          Resets only the local simulated paper account. Alpaca paper balances,
          positions, and orders are managed in Alpaca.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm">
            <span className="text-xs uppercase tracking-wide text-muted block mb-1">
              Starting cash
            </span>
            <input
              type="number"
              value={cash}
              onChange={(e) => setCash(parseFloat(e.target.value) || 0)}
              className="px-2 py-1.5 bg-panel border border-border rounded font-mono w-40"
              step={1000}
              min={1000}
            />
          </label>
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              disabled={!isLocalPaper}
              className="px-3 py-1.5 rounded bg-panel border border-border text-sm flex items-center gap-2 hover:bg-panel2 self-end disabled:opacity-50"
            >
              <RotateCcw size={14} /> Reset
            </button>
          ) : (
            <div className="flex items-center gap-2 self-end">
              <span className="text-xs text-bear">Wipes everything. Sure?</span>
              <button
                onClick={() => reset.mutate()}
                disabled={reset.isPending || !isLocalPaper}
                className="px-3 py-1.5 rounded bg-bear/20 text-bear border border-bear/30 text-sm flex items-center gap-2 hover:bg-bear/30 disabled:opacity-50"
              >
                {reset.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RotateCcw size={14} />
                )}
                Confirm
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="px-3 py-1.5 rounded bg-panel border border-border text-sm hover:bg-panel2"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
        {reset.data && (
          <div className="mt-3 text-sm text-bull">
            Reset complete. Starting cash: {formatUSD(reset.data.starting_cash)}.
          </div>
        )}
        {!isLocalPaper && (
          <div className="mt-3 text-xs text-muted">
            Active broker is {market.data?.broker ?? "loading"}; use Alpaca paper to reset that account.
          </div>
        )}
      </Card>
    </div>
  );
}
