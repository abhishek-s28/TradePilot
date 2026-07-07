"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";
import { CandlestickChart } from "@/components/CandlestickChart";
import { OrderPanel } from "@/components/OrderPanel";
import { useMarketStore } from "@/lib/marketStore";
import { useMarketWS } from "@/hooks/useMarketWS";
import { cn } from "@/lib/utils";
import {
  Activity, TrendingUp, TrendingDown, Zap, RefreshCw,
  ShieldAlert, Users, BarChart3, Bot, Wifi, WifiOff,
  ArrowUpRight, ArrowDownRight, Target, AlertTriangle,
} from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────────────────

interface Signal {
  id: string;
  symbol: string;
  strategy: string;
  asset_class: string;
  direction: "bullish" | "bearish" | "neutral";
  entry: number;
  stop_loss: number;
  take_profit: number;
  confidence: number;
  risk_reward?: number;
  status: string;
  generated_at: string;
  metadata?: Record<string, unknown>;
}

interface Position {
  symbol: string;
  qty: number;
  avg_price: number;
  current_price: number;
  unrealized_pnl: number;
  market_value: number;
  asset_class?: string;
}

interface Order {
  id: string;
  symbol: string;
  side: string;
  qty: string | number;
  order_type: string;
  limit_price?: number;
  status: string;
  submitted_at?: string;
  avg_fill_price?: number;
}

interface AutoStatus {
  enabled: boolean;
  kill_switch: boolean;
  trades_today: number;
  daily_pnl?: number;
}

interface ResearchTop {
  symbol: string;
  price: number;
  change_pct: number;
  trend: string;
  rsi: number;
  rel_volume: number;
  setup: string;
  grade: string;
  action: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmt(n: number | undefined | null, decimals = 2) {
  if (n == null || isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function signalGrade(s: Signal) {
  const rr  = Math.min((s.risk_reward ?? 1.5) / 4, 1);
  const score = s.confidence * 0.62 + rr * 0.3;
  if (score >= 0.72 && s.confidence >= 0.70) return "A+";
  if (score >= 0.60 && s.confidence >= 0.58) return "A";
  if (score >= 0.48) return "B";
  return "C";
}

const GRADE_CLS: Record<string, string> = {
  "A+": "text-emerald-400 bg-emerald-400/10 border-emerald-400/30",
  "A":  "text-bull bg-bull/10 border-bull/30",
  "B":  "text-yellow-400 bg-yellow-400/10 border-yellow-400/30",
  "C":  "text-muted bg-panel2 border-border",
};

const REGIME_CLS: Record<string, string> = {
  bullish:  "text-bull   bg-bull/10   border-bull/30",
  bearish:  "text-bear   bg-bear/10   border-bear/30",
  choppy:   "text-yellow-400 bg-yellow-400/10 border-yellow-400/30",
  high_vol: "text-orange-400 bg-orange-400/10 border-orange-400/30",
  unknown:  "text-muted bg-panel2 border-border",
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, trend,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  trend?: "up" | "down" | "flat";
}) {
  return (
    <div className="bg-panel border border-border rounded-lg p-4 flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-muted font-medium">{label}</span>
      <span className="text-xl font-bold font-mono tracking-tight flex items-center gap-1.5">
        {value}
        {trend === "up"   && <ArrowUpRight   size={14} className="text-bull" />}
        {trend === "down" && <ArrowDownRight  size={14} className="text-bear" />}
      </span>
      {sub && <span className="text-[10px] text-muted">{sub}</span>}
    </div>
  );
}

function SignalRow({ s }: { s: Signal }) {
  const bull  = s.direction === "bullish";
  const bear  = s.direction === "bearish";
  const grade = signalGrade(s);
  const isOpt = s.asset_class === "option";
  return (
    <div className={cn(
      "flex items-center gap-2 px-3 py-2.5 rounded-lg border transition-colors text-xs",
      bull ? "border-bull/15 bg-bull/3 hover:bg-bull/8"
           : bear ? "border-bear/15 bg-bear/3 hover:bg-bear/8"
           : "border-border bg-panel2 hover:bg-panel",
    )}>
      {bull
        ? <TrendingUp size={13} className="text-bull shrink-0" />
        : bear
        ? <TrendingDown size={13} className="text-bear shrink-0" />
        : <Activity size={13} className="text-muted shrink-0" />}

      <span className="font-mono font-bold w-16 truncate">{s.symbol}</span>

      <span className={cn(
        "text-[9px] px-1 py-0.5 rounded border font-bold shrink-0",
        GRADE_CLS[grade] ?? GRADE_CLS.C,
      )}>{grade}</span>

      {isOpt && (
        <span className="text-[9px] px-1 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 shrink-0">
          OPT
        </span>
      )}

      <span className="text-muted text-[10px] truncate flex-1">{s.strategy}</span>

      <span className="font-mono text-[10px] text-muted shrink-0">
        ${fmt(s.entry)}
      </span>

      <div className="flex items-center gap-1 shrink-0">
        <div className="w-10 h-1 bg-panel2 rounded-full overflow-hidden">
          <div
            className={cn("h-full rounded-full", bull ? "bg-bull" : bear ? "bg-bear" : "bg-muted")}
            style={{ width: `${(s.confidence * 100).toFixed(0)}%` }}
          />
        </div>
        <span className="text-[10px] font-mono">{(s.confidence * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

// ─── Main dashboard ───────────────────────────────────────────────────────────

export default function DashboardPage() {
  useMarketWS();
  const qc = useQueryClient();
  const { account: wsAccount, quotes, connected } = useMarketStore();

  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api<{ account: Record<string, number>; positions: Position[] }>("/portfolio"),
    refetchInterval: 8_000,
  });

  const signals = useQuery({
    queryKey: ["signals"],
    queryFn: () => api<Signal[]>("/signals?limit=30"),
    refetchInterval: 20_000,
  });

  const orders = useQuery({
    queryKey: ["orders"],
    queryFn: () => api<Order[]>("/v1/orders"),
    refetchInterval: 5_000,
  });

  const autoStatus = useQuery({
    queryKey: ["auto-status"],
    queryFn: () => api<AutoStatus>("/auto-trading/status"),
    refetchInterval: 10_000,
  });

  const marketStatus = useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<{ market_open: boolean; data_provider: string; broker: string }>("/market/status"),
    refetchInterval: 15_000,
  });

  const research = useQuery({
    queryKey: ["research-brief-dash"],
    queryFn: () => api<{
      market_regime: string; regime_color: string; regime_summary: string;
      spy_price: number; spy_change_pct: number; top_setups: ResearchTop[];
      analyst_notes: { analyst: { name: string; avatar: string }; headline: string; priority: string }[];
    }>("/research/brief"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const scan = useMutation({
    mutationFn: () => api("/signals/scan", { method: "POST", body: "{}" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      qc.invalidateQueries({ queryKey: ["research-brief-dash"] });
    },
  });

  const cancelOrder = useMutation({
    mutationFn: (id: string) => api(`/v1/orders/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });

  const acct      = wsAccount ?? portfolio.data?.account;
  const positions = portfolio.data?.positions ?? [];
  const ms        = marketStatus.data;
  const rb        = research.data;
  const as_       = autoStatus.data;

  const dailyPnl   = (acct?.daily_pnl as number) ?? 0;
  const equity     = (acct?.equity as number) ?? 0;
  const buyingPow  = (acct?.buying_power as number) ?? 0;
  const openPos    = (acct?.open_positions as number) ?? positions.length;

  const allSigs     = signals.data ?? [];
  const aPlusSigs   = allSigs.filter(s => signalGrade(s) === "A+");
  const bullSigs    = allSigs.filter(s => s.direction === "bullish");
  const bearSigs    = allSigs.filter(s => s.direction === "bearish");
  const optSigs     = allSigs.filter(s => s.asset_class === "option");
  const topSigs     = [...aPlusSigs, ...allSigs.filter(s => signalGrade(s) === "A")]
    .slice(0, 8);

  const regime     = rb?.market_regime ?? "unknown";
  const regimeCls  = REGIME_CLS[regime] ?? REGIME_CLS.unknown;

  return (
    <div className="space-y-4">

      {/* ── Status Strip ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-bold tracking-tight">Trading Terminal</h1>

        <div className="flex flex-wrap gap-1.5 ml-1">
          {ms && (
            <Pill tone={ms.market_open ? "bull" : "default"}>
              <Activity size={10} className="inline mr-1" />
              {ms.market_open ? "Market Open" : "Market Closed"}
            </Pill>
          )}
          <Pill tone={connected ? "bull" : "warn"}>
            {connected
              ? <><Wifi size={10} className="inline mr-1" />Live</>
              : <><WifiOff size={10} className="inline mr-1" />Reconnecting</>}
          </Pill>
          {rb && (
            <span className={cn(
              "flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide",
              regimeCls,
            )}>
              {regime}
            </span>
          )}
          {ms && (
            <Pill>{ms.data_provider} · {ms.broker}</Pill>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          {as_ && (
            <span className={cn(
              "flex items-center gap-1 px-2 py-1 rounded border text-[10px]",
              as_.enabled && !as_.kill_switch
                ? "bg-bull/10 border-bull/30 text-bull"
                : "bg-panel2 border-border text-muted",
            )}>
              <Bot size={10} />
              {as_.enabled && !as_.kill_switch ? `AutoBot • ${as_.trades_today} trades` : "AutoBot off"}
            </span>
          )}
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            <RefreshCw size={11} className={cn(scan.isPending && "animate-spin")} />
            {scan.isPending ? "Scanning…" : "Scan Now"}
          </button>
        </div>
      </div>

      {/* ── Account Stats ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Equity"
          value={equity ? `$${fmt(equity)}` : "—"}
          sub={openPos ? `${openPos} open position${openPos !== 1 ? "s" : ""}` : undefined}
        />
        <StatCard
          label="Day P&L"
          value={
            <span className={dailyPnl >= 0 ? "text-bull" : "text-bear"}>
              {equity ? `${dailyPnl >= 0 ? "+" : ""}$${fmt(dailyPnl)}` : "—"}
            </span>
          }
          trend={dailyPnl > 0 ? "up" : dailyPnl < 0 ? "down" : "flat"}
          sub={equity && dailyPnl ? `${((dailyPnl / equity) * 100).toFixed(2)}% today` : undefined}
        />
        <StatCard
          label="Buying Power"
          value={buyingPow ? `$${fmt(buyingPow)}` : "—"}
          sub="Available capital"
        />
        <StatCard
          label="Signals"
          value={allSigs.length}
          sub={
            allSigs.length
              ? `${aPlusSigs.length} A+ · ${bullSigs.length}↑ ${bearSigs.length}↓ · ${optSigs.length} opts`
              : "Run a scan"
          }
        />
      </div>

      {/* ── Regime + Research Banner ───────────────────────────────────────── */}
      {rb && (
        <div className={cn(
          "rounded-lg border p-4 flex flex-wrap items-start gap-4",
          regime === "bullish" ? "bg-bull/5 border-bull/20"
          : regime === "bearish" ? "bg-bear/5 border-bear/20"
          : "bg-panel border-border",
        )}>
          <div className="flex-1 min-w-48">
            <div className="flex items-center gap-2 mb-1">
              <BarChart3 size={12} className="text-accent" />
              <span className="text-[10px] font-semibold uppercase tracking-widest text-accent">
                Market Regime — {regime.toUpperCase()}
              </span>
              <span className={cn(
                "ml-auto text-xs font-mono font-semibold",
                rb.spy_change_pct >= 0 ? "text-bull" : "text-bear",
              )}>
                SPY ${rb.spy_price.toFixed(2)}
                <span className="ml-1">{rb.spy_change_pct >= 0 ? "+" : ""}{rb.spy_change_pct.toFixed(2)}%</span>
              </span>
            </div>
            <p className="text-xs text-muted">{rb.regime_summary}</p>
          </div>

          {rb.top_setups.length > 0 && (
            <div className="flex gap-2 flex-wrap">
              {rb.top_setups.slice(0, 5).map((s) => (
                <div key={s.symbol} className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs",
                  s.trend === "bullish"
                    ? "bg-bull/10 border-bull/30"
                    : "bg-bear/10 border-bear/30",
                )}>
                  <span className={cn(
                    "text-[9px] font-bold px-1 rounded border",
                    GRADE_CLS[s.grade] ?? GRADE_CLS.C,
                  )}>{s.grade}</span>
                  <span className="font-mono font-bold">{s.symbol}</span>
                  <span className="text-[10px] text-muted">{s.action}</span>
                  <span className={cn(
                    "text-[10px] font-mono",
                    s.change_pct >= 0 ? "text-bull" : "text-bear",
                  )}>
                    {s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Chart + Order Panel ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 bg-panel border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <BarChart3 size={13} className="text-accent" />
            <span className="text-sm font-semibold">Chart</span>
          </div>
          <CandlestickChart height={420} />
        </div>
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Target size={13} className="text-accent" />
            <span className="text-sm font-semibold">Quick Order</span>
          </div>
          <div className="h-[380px] overflow-y-auto">
            <OrderPanel />
          </div>
        </div>
      </div>

      {/* ── Top Signals ───────────────────────────────────────────────────── */}
      <div className="bg-panel border border-border rounded-lg">
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          <Zap size={13} className="text-accent" />
          <span className="text-sm font-semibold">Top Signals</span>
          <span className="text-[10px] text-muted ml-1">
            {aPlusSigs.length > 0 && (
              <span className="text-emerald-400 font-semibold">{aPlusSigs.length} A+ · </span>
            )}
            {allSigs.length} total
          </span>
          <a href="/signals" className="ml-auto text-[10px] text-accent hover:underline">
            View all →
          </a>
        </div>
        <div className="p-3">
          {signals.isLoading && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="h-9 rounded-lg bg-panel2 animate-pulse" />
              ))}
            </div>
          )}
          {!signals.isLoading && topSigs.length === 0 && (
            <div className="flex flex-col items-center py-8 gap-3 text-muted">
              <AlertTriangle size={20} className="text-muted/40" />
              <span className="text-sm">No signals yet.</span>
              <button
                onClick={() => scan.mutate()}
                disabled={scan.isPending}
                className="text-xs px-3 py-1.5 rounded bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20"
              >
                Run Scan
              </button>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {topSigs.map((s) => <SignalRow key={s.id} s={s} />)}
          </div>
        </div>
      </div>

      {/* ── Positions + Orders ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Positions */}
        <div className="bg-panel border border-border rounded-lg">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Activity size={13} className="text-accent" />
            <span className="text-sm font-semibold">Open Positions</span>
            <span className="text-[10px] text-muted ml-auto">live via WS</span>
          </div>
          <div className="p-0">
            {positions.length === 0 ? (
              <div className="text-sm text-muted text-center py-8">No open positions.</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[9px] uppercase text-muted border-b border-border">
                    {["Symbol", "Qty", "Avg", "Current", "P&L", "Value"].map((h) => (
                      <th key={h} className={cn("px-4 py-2 font-medium", h !== "Symbol" && "text-right")}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {positions.map((p) => {
                    const live = quotes[p.symbol]?.last ?? p.current_price;
                    const liveP = (live - p.avg_price) * p.qty;
                    const pos = liveP >= 0;
                    return (
                      <tr key={p.symbol} className="hover:bg-panel2 transition-colors">
                        <td className="px-4 py-2.5 font-mono font-bold">
                          {p.symbol}
                          {p.asset_class === "option" && (
                            <span className="ml-1 text-[9px] text-accent">OPT</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono">{p.qty}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-muted">${fmt(p.avg_price)}</td>
                        <td className="px-4 py-2.5 text-right font-mono">${fmt(live)}</td>
                        <td className={cn("px-4 py-2.5 text-right font-mono font-semibold", pos ? "text-bull" : "text-bear")}>
                          {pos ? "+" : ""}${fmt(liveP)}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-muted">${fmt(p.market_value)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Recent Orders */}
        <div className="bg-panel border border-border rounded-lg">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <ShieldAlert size={13} className="text-accent" />
            <span className="text-sm font-semibold">Recent Orders</span>
            <span className="text-[10px] text-muted ml-auto">refresh 5s</span>
          </div>
          <div className="p-0">
            {(!orders.data || orders.data.length === 0) ? (
              <div className="text-sm text-muted text-center py-8">No orders yet.</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[9px] uppercase text-muted border-b border-border">
                    {["Symbol", "Side", "Qty", "Fill", "Status", ""].map((h, i) => (
                      <th key={i} className={cn("px-4 py-2 font-medium", i > 1 && "text-right")}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {orders.data.slice(0, 8).map((o) => (
                    <tr key={o.id} className="hover:bg-panel2 transition-colors">
                      <td className="px-4 py-2.5 font-mono font-bold">{o.symbol}</td>
                      <td className="px-4 py-2.5">
                        <Pill tone={o.side === "buy" ? "bull" : "bear"}>{o.side}</Pill>
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono">{o.qty}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-muted">
                        {o.avg_fill_price ? `$${fmt(o.avg_fill_price)}` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Pill tone={
                          o.status === "filled" ? "bull"
                          : o.status === "rejected" ? "bear"
                          : o.status === "canceled" ? "default"
                          : "warn"
                        }>
                          {o.status}
                        </Pill>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {["new","partially_filled","accepted","submitted","pending"].includes(o.status) && (
                          <button
                            onClick={() => cancelOrder.mutate(o.id)}
                            className="text-[10px] text-bear hover:underline"
                          >
                            Cancel
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* ── Analyst Feed Preview ───────────────────────────────────────────── */}
      {rb && rb.analyst_notes.length > 0 && (
        <div className="bg-panel border border-border rounded-lg">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Users size={13} className="text-accent" />
            <span className="text-sm font-semibold">Research Desk</span>
            <span className="text-[10px] text-muted ml-1">10 analysts online</span>
            <a href="/research" className="ml-auto text-[10px] text-accent hover:underline">
              Full desk →
            </a>
          </div>
          <div className="divide-y divide-border">
            {rb.analyst_notes.slice(0, 3).map((n, i) => (
              <div key={i} className={cn(
                "px-4 py-3 flex items-start gap-3 border-l-2",
                n.priority === "high" ? "border-l-bear/60"
                : n.priority === "medium" ? "border-l-yellow-400/40"
                : "border-l-border",
              )}>
                <span className="w-7 h-7 rounded-full bg-accent/20 border border-accent/30 text-[9px] font-bold text-accent flex items-center justify-center shrink-0">
                  {n.analyst.avatar}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold">{n.analyst.name}</span>
                    {n.priority === "high" && (
                      <AlertTriangle size={10} className="text-bear" />
                    )}
                  </div>
                  <p className="text-[10px] text-muted mt-0.5 truncate">{n.headline}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
