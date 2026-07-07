"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Stat, Pill } from "@/components/primitives";
import { useMarketStore } from "@/lib/marketStore";
import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, X } from "lucide-react";

interface Position {
  symbol: string;
  asset_class: string;
  qty: number;
  avg_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  market_value: number;
}

interface Account {
  equity: number;
  cash: number;
  buying_power: number;
  positions_value: number;
  daily_pnl: number;
  open_positions: number;
}

export default function PortfolioPage() {
  const qc = useQueryClient();
  const { quotes, account: wsAcct } = useMarketStore();

  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api<{ account: Account; positions: Position[] }>("/portfolio"),
    refetchInterval: 5_000,
  });

  const closePos = useMutation({
    mutationFn: (symbol: string) =>
      api(`/positions/${symbol}/close`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio"] }),
  });

  const acct      = wsAcct ?? portfolio.data?.account;
  const positions = portfolio.data?.positions ?? [];

  const fmt = (n: number) =>
    n?.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const pnlCls = (v: number) => v >= 0 ? "text-bull" : "text-bear";

  const totalUnreal = positions.reduce((s, p) => s + p.unrealized_pnl, 0);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold tracking-tight">Portfolio</h1>

      {/* Account stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Equity" value={acct ? `$${fmt(acct.equity)}` : "—"} />
        <Stat
          label="Day P&L"
          value={
            <span className={pnlCls(acct?.daily_pnl ?? 0)}>
              {acct ? `${acct.daily_pnl >= 0 ? "+" : ""}$${fmt(acct.daily_pnl)}` : "—"}
            </span>
          }
        />
        <Stat label="Cash" value={acct ? `$${fmt(acct.cash)}` : "—"} />
        <Stat label="Buying Power" value={acct ? `$${fmt(acct.buying_power)}` : "—"} />
      </div>

      {/* Summary bar */}
      {positions.length > 0 && (
        <div className="flex flex-wrap gap-4 px-4 py-3 bg-panel border border-border rounded-lg text-sm">
          <span className="text-muted">
            {positions.length} position{positions.length !== 1 ? "s" : ""}
          </span>
          <span className="text-muted">
            Market value: <span className="font-mono text-text">${fmt(acct?.positions_value ?? 0)}</span>
          </span>
          <span className={cn("font-mono font-semibold", pnlCls(totalUnreal))}>
            Unrealized: {totalUnreal >= 0 ? "+" : ""}${fmt(totalUnreal)}
          </span>
        </div>
      )}

      {/* Positions table */}
      <Card title="Positions">
        {portfolio.isLoading && (
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-10 bg-panel2 rounded animate-pulse" />
            ))}
          </div>
        )}

        {!portfolio.isLoading && positions.length === 0 && (
          <div className="text-sm text-muted py-4 text-center">
            No open positions. Place a paper trade from the Dashboard.
          </div>
        )}

        {positions.length > 0 && (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[10px] uppercase text-muted border-b border-border">
                  <th className="pb-2 text-left">Symbol</th>
                  <th className="pb-2 text-left">Type</th>
                  <th className="pb-2 text-right">Qty</th>
                  <th className="pb-2 text-right">Avg Cost</th>
                  <th className="pb-2 text-right">Current</th>
                  <th className="pb-2 text-right">Mkt Value</th>
                  <th className="pb-2 text-right">Unrealized P&L</th>
                  <th className="pb-2 text-right">Unrealized %</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {positions.map((p) => {
                  const live    = quotes[p.symbol]?.last ?? p.current_price;
                  const unreal  = p.unrealized_pnl;
                  const unPct   = p.avg_price > 0
                    ? ((live - p.avg_price) / p.avg_price) * 100
                    : 0;
                  return (
                    <tr key={p.symbol} className="group">
                      <td className="py-3 font-mono font-bold">{p.symbol}</td>
                      <td className="py-3">
                        <Pill>{p.asset_class}</Pill>
                      </td>
                      <td className="py-3 text-right font-mono">{p.qty}</td>
                      <td className="py-3 text-right font-mono text-muted">${fmt(p.avg_price)}</td>
                      <td className="py-3 text-right font-mono">${fmt(live)}</td>
                      <td className="py-3 text-right font-mono">${fmt(p.qty * live)}</td>
                      <td className={cn("py-3 text-right font-mono font-semibold", pnlCls(unreal))}>
                        {unreal >= 0 ? "+" : ""}${fmt(unreal)}
                      </td>
                      <td className={cn("py-3 text-right font-mono", pnlCls(unPct))}>
                        {unPct >= 0 ? "+" : ""}{unPct.toFixed(2)}%
                      </td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => {
                            if (confirm(`Close ${p.symbol} position?`)) closePos.mutate(p.symbol);
                          }}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-bear hover:text-bear/80"
                        >
                          <X size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
