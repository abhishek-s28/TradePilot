"use client";
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { api } from "@/lib/api";
import { Card, Stat, Pill } from "@/components/primitives";
import { formatUSD, formatPct, cn } from "@/lib/utils";

interface Stats {
  total_trades: number;
  win_trades: number;
  loss_trades: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  best_trade: number;
  worst_trade: number;
  starting_cash: number;
  total_return_pct: number;
}

interface EquityPoint {
  time: string | null;
  equity: number;
  trade: number;
  pnl?: number;
  symbol?: string;
  outcome?: "win" | "loss";
}

interface DailyPnl {
  date: string;
  pnl: number;
  positive: boolean;
}

interface JournalEntry {
  id: string;
  symbol: string;
  asset_class: string;
  qty: number;
  avg_entry: number;
  realized_pnl: number;
  pnl_pct: number;
  opened_at: string;
  closed_at: string;
  duration_minutes: number;
  outcome: "win" | "loss" | "flat";
}

export default function AnalyticsPage() {
  const stats = useQuery({
    queryKey: ["analytics-stats"],
    queryFn: () => api<Stats>("/analytics/stats"),
    refetchInterval: 15_000,
  });

  const equity = useQuery({
    queryKey: ["analytics-equity"],
    queryFn: () => api<EquityPoint[]>("/analytics/equity-curve"),
    refetchInterval: 15_000,
  });

  const daily = useQuery({
    queryKey: ["analytics-daily"],
    queryFn: () => api<DailyPnl[]>("/analytics/daily-pnl"),
    refetchInterval: 15_000,
  });

  const journal = useQuery({
    queryKey: ["analytics-journal"],
    queryFn: () => api<JournalEntry[]>("/analytics/journal"),
    refetchInterval: 15_000,
  });

  const s = stats.data;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Analytics & Performance</h1>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat
          label="Total return"
          value={
            <span className={(s?.total_pnl ?? 0) >= 0 ? "text-bull" : "text-bear"}>
              {s ? `${s.total_return_pct > 0 ? "+" : ""}${s.total_return_pct.toFixed(2)}%` : "—"}
            </span>
          }
        />
        <Stat
          label="Realized P&L"
          value={
            <span className={(s?.total_pnl ?? 0) >= 0 ? "text-bull" : "text-bear"}>
              {s ? formatUSD(s.total_pnl) : "—"}
            </span>
          }
        />
        <Stat label="Total trades" value={s?.total_trades ?? "—"} />
        <Stat
          label="Win rate"
          value={s ? `${s.win_rate.toFixed(1)}%` : "—"}
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Sharpe ratio" value={s ? s.sharpe_ratio.toFixed(2) : "—"} />
        <Stat label="Profit factor" value={s ? s.profit_factor.toFixed(2) : "—"} />
        <Stat
          label="Max drawdown"
          value={
            <span className="text-bear">
              {s ? `${s.max_drawdown_pct.toFixed(2)}%` : "—"}
            </span>
          }
        />
        <Stat label="Avg per trade" value={s ? formatUSD(s.avg_pnl) : "—"} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Avg win" value={s ? <span className="text-bull">{formatUSD(s.avg_win)}</span> : "—"} />
        <Stat label="Avg loss" value={s ? <span className="text-bear">{formatUSD(s.avg_loss)}</span> : "—"} />
        <Stat label="Best trade" value={s ? <span className="text-bull">{formatUSD(s.best_trade)}</span> : "—"} />
        <Stat label="Worst trade" value={s ? <span className="text-bear">{formatUSD(s.worst_trade)}</span> : "—"} />
      </div>

      {/* Equity curve */}
      <Card title="Equity curve">
        {(equity.data?.length ?? 0) <= 1 ? (
          <div className="text-sm text-muted py-4">
            No closed trades yet — equity curve will appear once positions are closed.
          </div>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equity.data}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis
                  dataKey="trade"
                  tick={{ fontSize: 10, fill: "#6b7280" }}
                  tickLine={false}
                  label={{ value: "Trade #", position: "insideBottom", dy: 12, fill: "#6b7280", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "#6b7280" }}
                  tickLine={false}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  width={52}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1a1a2e", border: "1px solid #333", borderRadius: 6 }}
                  labelStyle={{ color: "#9ca3af" }}
                  formatter={(v: number) => [formatUSD(v), "Equity"]}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="#22c55e"
                  strokeWidth={2}
                  fill="url(#equityGrad)"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      {/* Daily P&L bar chart */}
      {(daily.data?.length ?? 0) > 0 && (
        <Card title="Daily realized P&L">
          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={daily.data}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} tickLine={false} tickFormatter={(v) => `$${v}`} width={52} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1a1a2e", border: "1px solid #333", borderRadius: 6 }}
                  formatter={(v: number) => [formatUSD(v), "P&L"]}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Bar
                  dataKey="pnl"
                  radius={[2, 2, 0, 0]}
                  fill="#22c55e"
                  // @ts-ignore — recharts cell colouring via function
                  // eslint-disable-next-line react/display-name
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {/* Trade journal */}
      <Card title={`Trade journal (${journal.data?.length ?? 0} closed trades)`}>
        {(journal.data?.length ?? 0) === 0 ? (
          <div className="text-sm text-muted">
            No closed trades yet. Enable auto-trading and let the bot run.
          </div>
        ) : (
          <div className="overflow-x-auto -mx-4">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-muted uppercase">
                <tr className="border-b border-border">
                  <th className="py-2 px-3 text-left">Symbol</th>
                  <th className="py-2 px-3 text-left">Class</th>
                  <th className="py-2 px-3 text-right">Qty</th>
                  <th className="py-2 px-3 text-right">Entry</th>
                  <th className="py-2 px-3 text-right">P&L</th>
                  <th className="py-2 px-3 text-right">P&L %</th>
                  <th className="py-2 px-3 text-right">Duration</th>
                  <th className="py-2 px-3 text-left">Closed</th>
                  <th className="py-2 px-3 text-center">Result</th>
                </tr>
              </thead>
              <tbody>
                {(journal.data ?? []).map((t) => (
                  <tr key={t.id} className="border-b border-border/50 hover:bg-panel2/40">
                    <td className="py-2 px-3 font-mono font-medium">{t.symbol}</td>
                    <td className="py-2 px-3">
                      <Pill>{t.asset_class}</Pill>
                    </td>
                    <td className="py-2 px-3 text-right font-mono">{t.qty}</td>
                    <td className="py-2 px-3 text-right font-mono">{formatUSD(t.avg_entry)}</td>
                    <td className={cn(
                      "py-2 px-3 text-right font-mono",
                      t.realized_pnl >= 0 ? "text-bull" : "text-bear"
                    )}>
                      {formatUSD(t.realized_pnl)}
                    </td>
                    <td className={cn(
                      "py-2 px-3 text-right font-mono text-xs",
                      t.pnl_pct >= 0 ? "text-bull" : "text-bear"
                    )}>
                      {t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                    </td>
                    <td className="py-2 px-3 text-right text-xs text-muted">
                      {t.duration_minutes ? `${t.duration_minutes}m` : "—"}
                    </td>
                    <td className="py-2 px-3 text-xs text-muted">
                      {t.closed_at ? new Date(t.closed_at).toLocaleString() : "—"}
                    </td>
                    <td className="py-2 px-3 text-center">
                      <Pill tone={t.outcome === "win" ? "bull" : t.outcome === "loss" ? "bear" : "default"}>
                        {t.outcome}
                      </Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
