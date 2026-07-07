"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";

interface StrategyMeta {
  name: string;
  description: string;
  timeframe: string;
  lookback_bars: number;
  default_params: Record<string, unknown>;
}

export default function StrategiesPage() {
  const strategies = useQuery({
    queryKey: ["strategies"],
    queryFn: () => api<StrategyMeta[]>("/strategies"),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Strategies</h1>

      <Card>
        <p className="text-sm text-muted">
          Strategies that the signal scanner runs against your watchlist. The pack covers trend,
          breakout, pullback, reversal, VWAP, volatility compression, and option-suitable setups.
          Every generated idea still passes through the risk manager before any paper order.
        </p>
      </Card>

      {strategies.isLoading && <div className="text-sm text-muted">Loading…</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {(strategies.data || []).map((s) => (
          <Card key={s.name} title={
            <span className="flex items-center gap-2">
              <span className="font-mono">{s.name}</span>
              <Pill>{s.timeframe}</Pill>
            </span>
          }>
            <p className="text-sm text-muted mb-3">{s.description || "No description."}</p>
            <div className="text-xs text-muted">
              Lookback: {s.lookback_bars} bars
            </div>
            {Object.keys(s.default_params).length > 0 && (
              <div className="mt-3">
                <div className="text-xs uppercase tracking-wide text-muted mb-1">
                  Default params
                </div>
                <pre className="text-xs bg-panel2/60 border border-border rounded p-2 overflow-x-auto font-mono">
{JSON.stringify(s.default_params, null, 2)}
                </pre>
              </div>
            )}
          </Card>
        ))}
      </div>

      {strategies.data && strategies.data.length === 0 && (
        <div className="text-sm text-muted">No strategies registered.</div>
      )}
    </div>
  );
}
