"use client";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";
import { cn } from "@/lib/utils";
import { Search } from "lucide-react";

interface OptionContract {
  symbol: string;
  underlying: string;
  expiration: string;
  strike: number;
  right: "call" | "put";
  bid: number;
  ask: number;
  mid: number;
  volume: number;
  open_interest: number;
  implied_volatility: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  spread_pct: number;
  liquidity_score: number;
}

export default function OptionsPage() {
  const [ticker, setTicker] = useState("AAPL");
  const [input,  setInput]  = useState("AAPL");
  const [expiry, setExpiry]  = useState<string | null>(null);

  const chain = useQuery({
    queryKey: ["options-chain", ticker],
    queryFn: () => api<OptionContract[]>(`/options/chain/${ticker}`),
    enabled: !!ticker,
  });

  const fmt = (n?: number | null, dp = 2) =>
    n != null
      ? n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })
      : "—";

  // Get unique expiry dates
  const expiries = useMemo(() => {
    const dates = [...new Set((chain.data ?? []).map((c) => c.expiration.slice(0, 10)))].sort();
    if (!expiry && dates.length) setExpiry(dates[0]);
    return dates;
  }, [chain.data]);

  const filtered = useMemo(() => {
    if (!expiry) return chain.data ?? [];
    return (chain.data ?? []).filter((c) => c.expiration.startsWith(expiry));
  }, [chain.data, expiry]);

  // Split into calls / puts, keyed by strike
  const strikes = useMemo(() => {
    const map = new Map<number, { call?: OptionContract; put?: OptionContract }>();
    filtered.forEach((c) => {
      const entry = map.get(c.strike) ?? {};
      if (c.right === "call") entry.call = c;
      else entry.put = c;
      map.set(c.strike, entry);
    });
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [filtered]);

  // High OI threshold
  const maxOI = useMemo(
    () => Math.max(...filtered.map((c) => c.open_interest), 1),
    [filtered],
  );

  const ivRank = useMemo(() => {
    const ivs = filtered.map((c) => c.implied_volatility ?? 0).filter(Boolean);
    if (!ivs.length) return null;
    const avg = ivs.reduce((s, v) => s + v, 0) / ivs.length;
    return Math.min(100, avg * 100);
  }, [filtered]);

  const contractCell = (c?: OptionContract, side: "call" | "put" = "call") => {
    if (!c) return <td colSpan={5} className="py-2 text-muted text-center">—</td>;
    const isCall = side === "call";
    return (
      <>
        <td className="py-2 text-right font-mono text-muted">{fmt(c.bid)}</td>
        <td className="py-2 text-right font-mono">{fmt(c.ask)}</td>
        <td className="py-2 text-right font-mono text-xs text-muted">{c.open_interest.toLocaleString()}</td>
        <td className="py-2 text-right font-mono text-xs text-muted">
          {c.implied_volatility != null ? `${(c.implied_volatility * 100).toFixed(1)}%` : "—"}
        </td>
        <td className="py-2 text-right font-mono text-xs text-muted">
          {c.delta != null ? fmt(c.delta, 3) : "—"}
        </td>
      </>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">Options Chain</h1>

        {/* Symbol search */}
        <form onSubmit={(e) => { e.preventDefault(); setTicker(input.trim().toUpperCase()); }}
          className="flex">
          <div className="relative">
            <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value.toUpperCase())}
              placeholder="AAPL"
              className="pl-7 pr-2 py-1.5 bg-panel2 border border-border rounded-l text-sm font-mono focus:outline-none focus:border-accent w-24"
            />
          </div>
          <button type="submit"
            className="px-3 py-1.5 bg-accent/20 border border-border border-l-0 rounded-r text-xs text-accent hover:bg-accent/30">
            Load
          </button>
        </form>

        {/* IV Rank gauge */}
        {ivRank !== null && (
          <div className="flex items-center gap-2 ml-auto text-xs">
            <span className="text-muted">Avg IV</span>
            <div className="w-24 h-2 bg-panel2 rounded-full overflow-hidden">
              <div
                className={cn("h-full rounded-full transition-all",
                  ivRank > 70 ? "bg-bear" : ivRank < 30 ? "bg-bull" : "bg-accent")}
                style={{ width: `${ivRank.toFixed(0)}%` }}
              />
            </div>
            <span className="font-mono">{ivRank.toFixed(0)}%</span>
            {ivRank > 70 && <Pill tone="bear">Sell Premium</Pill>}
            {ivRank < 30 && <Pill tone="bull">Buy Premium</Pill>}
          </div>
        )}
      </div>

      {/* Expiry tabs */}
      {expiries.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {expiries.map((d) => (
            <button key={d} onClick={() => setExpiry(d)}
              className={cn("px-3 py-1 text-xs rounded border",
                expiry === d
                  ? "border-accent/50 bg-accent/10 text-accent"
                  : "border-border bg-panel2 text-muted hover:text-text")}>
              {d}
            </button>
          ))}
        </div>
      )}

      {/* Chain table */}
      <Card>
        {chain.isLoading && (
          <div className="space-y-2">
            {[...Array(8)].map((_, i) => (
              <div key={i} className="h-8 bg-panel2 rounded animate-pulse" />
            ))}
          </div>
        )}
        {chain.isError && (
          <div className="text-bear text-sm py-4 text-center">
            Failed to load chain. Options data requires an options-enabled Alpaca account.
          </div>
        )}
        {!chain.isLoading && !chain.isError && strikes.length === 0 && (
          <div className="text-muted text-sm py-4 text-center">
            No contracts found for {ticker} on {expiry}.
          </div>
        )}
        {strikes.length > 0 && (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[10px] uppercase text-muted border-b border-border">
                  {/* Calls */}
                  <th className="pb-2 text-right text-bull/70">Bid</th>
                  <th className="pb-2 text-right text-bull/70">Ask</th>
                  <th className="pb-2 text-right text-bull/70">OI</th>
                  <th className="pb-2 text-right text-bull/70">IV</th>
                  <th className="pb-2 text-right text-bull/70">Δ</th>
                  {/* Strike */}
                  <th className="pb-2 text-center px-4 text-text font-bold">Strike</th>
                  {/* Puts */}
                  <th className="pb-2 text-left text-bear/70">Bid</th>
                  <th className="pb-2 text-left text-bear/70">Ask</th>
                  <th className="pb-2 text-left text-bear/70">OI</th>
                  <th className="pb-2 text-left text-bear/70">IV</th>
                  <th className="pb-2 text-left text-bear/70">Δ</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {strikes.map(([strike, { call, put }]) => {
                  const highOI = (call?.open_interest ?? 0) > maxOI * 0.7
                    || (put?.open_interest ?? 0) > maxOI * 0.7;
                  return (
                    <tr key={strike} className={cn(highOI && "ring-1 ring-inset ring-accent/20 bg-accent/5")}>
                      {contractCell(call, "call")}
                      <td className="py-2 text-center px-4 font-mono font-bold text-text bg-panel2">
                        ${fmt(strike)}
                      </td>
                      {contractCell(put, "put")}
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
