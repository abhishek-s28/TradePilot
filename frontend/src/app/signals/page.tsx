"use client";
import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";
import { cn } from "@/lib/utils";
import {
  TrendingUp, TrendingDown, RefreshCw, Minus,
  Copy, Check, Star, Zap, AlertTriangle,
  ArrowUp, ArrowDown,
} from "lucide-react";

interface QuoteResponse {
  symbol: string;
  mid: number;
  last: number;
  bid: number;
  ask: number;
}

function LivePnL({ signal }: { signal: Signal }) {
  const isOption = signal.asset_class === "option";
  const underlying = (signal.metadata?.underlying_symbol as string | undefined) ?? signal.symbol;
  const quoteSymbol = isOption ? underlying : signal.symbol;

  const quote = useQuery({
    queryKey: ["live-quote", quoteSymbol],
    queryFn: () => api<QuoteResponse>(`/v1/quote/${quoteSymbol}`),
    refetchInterval: 10_000,
    staleTime: 8_000,
    enabled: signal.status === "paper_executed",
  });

  if (signal.status !== "paper_executed" || !quote.data) return null;

  const currentPrice = quote.data.mid || quote.data.last;
  const qty = signal.suggested_qty ?? 1;

  let pnl: number;
  if (isOption) {
    const delta = (signal.metadata?.selected_option as Record<string, number> | undefined)?.delta ?? 0.45;
    const underlyingEntry = (signal.metadata?.underlying_entry as number | undefined) ?? signal.entry;
    pnl = (currentPrice - underlyingEntry) * delta * qty * 100;
  } else {
    const dir = signal.direction === "bearish" ? -1 : 1;
    pnl = (currentPrice - signal.entry) * qty * dir;
  }

  const positive = pnl >= 0;
  return (
    <div className={cn(
      "flex items-center gap-1 text-xs font-mono font-semibold px-2 py-0.5 rounded",
      positive ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear",
    )}>
      {positive ? <ArrowUp size={10} /> : <ArrowDown size={10} />}
      {positive ? "+" : ""}${pnl.toFixed(2)}
    </div>
  );
}

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
  reason: string;
  status: string;
  generated_at: string;
  risk_reward?: number;
  suggested_qty?: number;
  metadata?: Record<string, unknown>;
}

const FILTERS = ["all", "bullish", "bearish", "stocks", "options", "A+", "A"] as const;
type Filter = (typeof FILTERS)[number];

function signalGrade(s: Signal): string {
  const conf = s.confidence;
  const rr = s.risk_reward ?? 1;
  const score = conf * 0.6 + Math.min(rr / 5, 1) * 0.4;
  if (score >= 0.72 && conf >= 0.70) return "A+";
  if (score >= 0.60 && conf >= 0.58) return "A";
  if (score >= 0.48) return "B";
  return "C";
}

function gradeColor(g: string) {
  if (g === "A+") return "text-emerald-400 bg-emerald-400/10 border-emerald-400/30";
  if (g === "A")  return "text-bull bg-bull/10 border-bull/30";
  if (g === "B")  return "text-yellow-400 bg-yellow-400/10 border-yellow-400/30";
  return "text-muted bg-panel2 border-border";
}

function buildBrokerText(s: Signal): string {
  const isOption = s.asset_class === "option";
  const bull = s.direction === "bullish";
  const qty = s.suggested_qty ?? 1;
  const fmt = (n: number) => n.toFixed(2);
  const pct = s.risk_reward ? `(R:R ${s.risk_reward.toFixed(1)}:1)` : "";

  if (isOption) {
    const opt = s.metadata?.selected_option as Record<string, unknown> | undefined;
    const contract = opt?.symbol as string ?? s.symbol;
    const mid = opt?.mid as number ?? s.entry;
    const strike = opt?.strike as number | undefined;
    const exp = opt?.expiration as string | undefined;
    const right = opt?.right as string ?? (bull ? "CALL" : "PUT");
    const underlying = (s.metadata?.underlying_symbol as string) ?? s.symbol.slice(0, 4);
    const expStr = exp ? new Date(exp).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" }) : "";

    return [
      `📋 COPY TRADE — OPTIONS`,
      `Action:     BUY TO OPEN`,
      `Contract:   ${contract}`,
      `Underlying: ${underlying}  ${right.toUpperCase()}  ${strike ? `$${strike}` : ""}  Exp ${expStr}`,
      `Qty:        ${qty} contract${qty > 1 ? "s" : ""}`,
      `Order:      LIMIT @ $${fmt(mid)} (or MKT if liquid)`,
      `Stop:       CLOSE BELOW $${fmt(s.stop_loss)} (≈50% loss)`,
      `Target:     CLOSE ABOVE $${fmt(s.take_profit)} ${pct}`,
      `Strategy:   ${s.strategy}`,
      `Confidence: ${(s.confidence * 100).toFixed(0)}%  Grade: ${signalGrade(s)}`,
      `Note:       ${s.reason.slice(0, 120)}`,
    ].join("\n");
  }

  return [
    `📋 COPY TRADE — ${bull ? "LONG" : "SHORT"} STOCK`,
    `Action:     ${bull ? "BUY" : "SELL SHORT"} ${s.symbol}`,
    `Qty:        ${qty} share${qty > 1 ? "s" : ""}`,
    `Entry:      LIMIT @ $${fmt(s.entry)}`,
    `Stop Loss:  $${fmt(s.stop_loss)}`,
    `Target:     $${fmt(s.take_profit)} ${pct}`,
    `Strategy:   ${s.strategy}`,
    `Confidence: ${(s.confidence * 100).toFixed(0)}%  Grade: ${signalGrade(s)}`,
    `Note:       ${s.reason.slice(0, 120)}`,
  ].join("\n");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className={cn(
        "flex items-center gap-1 text-[10px] px-2 py-1 rounded border transition-colors",
        copied
          ? "bg-bull/20 text-bull border-bull/40"
          : "bg-accent/10 text-accent border-accent/30 hover:bg-accent/20",
      )}
      title="Copy broker-ready trade to clipboard"
    >
      {copied ? <Check size={10} /> : <Copy size={10} />}
      {copied ? "Copied!" : "Copy Trade"}
    </button>
  );
}

function SignalCard({ s }: { s: Signal }) {
  const bull = s.direction === "bullish";
  const bear = s.direction === "bearish";
  const isOption = s.asset_class === "option";
  const grade = signalGrade(s);
  const opt = s.metadata?.selected_option as Record<string, unknown> | undefined;
  const optionsExpression =
    typeof s.metadata?.options_expression === "string"
      ? s.metadata.options_expression.replace(/_/g, " ")
      : null;
  const fmt = (n: number) =>
    n?.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className={cn(
      "bg-panel border rounded-lg p-4 flex flex-col gap-3 relative",
      bull ? "border-bull/20" : bear ? "border-bear/20" : "border-border",
    )}>
      {/* Grade badge */}
      <span className={cn(
        "absolute top-3 right-3 text-[10px] font-bold px-1.5 py-0.5 rounded border",
        gradeColor(grade),
      )}>
        {grade}
      </span>

      {/* Header */}
      <div className="flex items-start justify-between pr-8">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-base">{s.symbol}</span>
            {bull ? <TrendingUp size={15} className="text-bull" />
              : bear ? <TrendingDown size={15} className="text-bear" />
              : <Minus size={15} className="text-muted" />}
            <Pill tone={bull ? "bull" : bear ? "bear" : "default"}>{s.direction}</Pill>
            {isOption && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                OPT
              </span>
            )}
          </div>
          <div className="text-xs text-muted mt-0.5">{s.strategy}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="text-xs text-muted">Confidence</span>
          <div className="flex items-center gap-1">
            <div className="w-14 h-1.5 bg-panel2 rounded-full overflow-hidden">
              <div
                className={cn("h-full rounded-full", bull ? "bg-bull" : bear ? "bg-bear" : "bg-muted")}
                style={{ width: `${(s.confidence * 100).toFixed(0)}%` }}
              />
            </div>
            <span className="text-xs font-mono">{(s.confidence * 100).toFixed(0)}%</span>
          </div>
        </div>
      </div>

      {/* Price levels */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="bg-panel2 rounded p-2">
          <div className="text-muted mb-0.5">Entry</div>
          <div className="font-mono font-semibold">${fmt(s.entry)}</div>
        </div>
        <div className="bg-bear/10 rounded p-2">
          <div className="text-muted mb-0.5">Stop</div>
          <div className="font-mono font-semibold text-bear">${fmt(s.stop_loss)}</div>
        </div>
        <div className="bg-bull/10 rounded p-2">
          <div className="text-muted mb-0.5">Target</div>
          <div className="font-mono font-semibold text-bull">${fmt(s.take_profit)}</div>
        </div>
      </div>

      {/* Option contract details */}
      {isOption && opt && (
        <div className="bg-accent/5 border border-accent/20 rounded p-2 text-xs space-y-1">
          <div className="text-accent font-semibold text-[10px] uppercase tracking-wide">Options Contract</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
            <span className="text-muted">Contract</span>
            <span className="text-text truncate">{opt.symbol as string}</span>
            <span className="text-muted">Right</span>
            <span className={cn(
              "font-semibold",
              (opt.right as string) === "call" ? "text-bull" : "text-bear",
            )}>
              {(opt.right as string)?.toUpperCase()}
            </span>
            <span className="text-muted">Strike</span>
            <span>${opt.strike as number}</span>
            <span className="text-muted">Expiry</span>
            <span>{new Date(opt.expiration as string).toLocaleDateString("en-US", {
              month: "short", day: "numeric", year: "2-digit",
            })}</span>
            {opt.delta != null && (
              <>
                <span className="text-muted">Delta</span>
                <span>{(opt.delta as number).toFixed(2)}</span>
              </>
            )}
            {(opt.bid as number) > 0 && (
              <>
                <span className="text-muted">Bid / Ask</span>
                <span>${(opt.bid as number).toFixed(2)} / ${(opt.ask as number).toFixed(2)}</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* R:R and qty */}
      {s.risk_reward && (
        <div className="text-xs text-muted">
          R:R <span className="font-mono text-text">{s.risk_reward.toFixed(2)}</span>
          {s.suggested_qty && (
            <span className="ml-3">
              Qty <span className="font-mono text-text">{s.suggested_qty}</span>
            </span>
          )}
          {optionsExpression && (
            <span className="ml-3 text-[10px] text-accent/70">
              {optionsExpression}
            </span>
          )}
        </div>
      )}

      {/* Reason */}
      <p className="text-xs text-muted leading-relaxed line-clamp-2">{s.reason}</p>

      {/* Footer actions */}
      <div className="flex items-center gap-2 mt-auto pt-2 border-t border-border flex-wrap">
        <Pill
          tone={
            s.status === "paper_executed" ? "bull"
            : s.status === "rejected" ? "bear"
            : "default"
          }
        >
          {s.status}
        </Pill>
        <LivePnL signal={s} />
        <span className="text-[10px] text-muted">
          {new Date(s.generated_at).toLocaleTimeString()}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <CopyButton text={buildBrokerText(s)} />
        </div>
      </div>
    </div>
  );
}

export default function SignalsPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");

  const signals = useQuery({
    queryKey: ["signals-full"],
    queryFn: () => api<Signal[]>("/signals?limit=80"),
    refetchInterval: 30_000,
  });

  const scan = useMutation({
    mutationFn: () => api("/signals/scan", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["signals-full"] });
      qc.invalidateQueries({ queryKey: ["signals"] });
    },
  });

  const all = signals.data ?? [];
  const filtered = all.filter((s) => {
    if (filter === "bullish") return s.direction === "bullish";
    if (filter === "bearish") return s.direction === "bearish";
    if (filter === "stocks")  return s.asset_class === "stock";
    if (filter === "options") return s.asset_class === "option";
    if (filter === "A+")      return signalGrade(s) === "A+";
    if (filter === "A")       return signalGrade(s) === "A" || signalGrade(s) === "A+";
    return true;
  });

  // Stats
  const aPlus = all.filter(s => signalGrade(s) === "A+").length;
  const aGrade = all.filter(s => signalGrade(s) === "A").length;
  const bullCount = all.filter(s => s.direction === "bullish").length;
  const bearCount = all.filter(s => s.direction === "bearish").length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">Signals</h1>

        {/* Grade stats */}
        <div className="flex items-center gap-2 text-xs">
          {aPlus > 0 && (
            <span className="flex items-center gap-1 px-2 py-1 rounded border border-emerald-400/30 bg-emerald-400/10 text-emerald-400">
              <Star size={10} /> {aPlus} A+
            </span>
          )}
          {aGrade > 0 && (
            <span className="flex items-center gap-1 px-2 py-1 rounded border border-bull/30 bg-bull/10 text-bull">
              <Zap size={10} /> {aGrade} A
            </span>
          )}
          <span className="text-muted">{bullCount}↑ {bearCount}↓</span>
        </div>

        {/* Filters */}
        <div className="flex rounded border border-border overflow-hidden">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-3 py-1.5 text-xs capitalize",
                filter === f
                  ? "bg-accent/20 text-accent font-semibold"
                  : "bg-panel2 text-muted hover:text-text",
              )}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Scan button */}
        <button
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50"
        >
          <RefreshCw size={11} className={cn(scan.isPending && "animate-spin")} />
          {scan.isPending ? "Scanning…" : "Run Scan"}
        </button>
      </div>

      {/* Copy-trade instruction banner */}
      <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-accent/5 border border-accent/20 text-xs text-muted">
        <Copy size={12} className="text-accent mt-0.5 shrink-0" />
        <span>
          Click <span className="text-accent font-semibold">Copy Trade</span> on any signal to get a broker-ready
          order block. Paste it into your brokerage or send it to your broker chat.
          Grades A+/A are highest-conviction — prioritize those for live trading.
        </span>
      </div>

      {/* Loading */}
      {signals.isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="bg-panel border border-border rounded-lg h-52 animate-pulse" />
          ))}
        </div>
      )}

      {/* Empty */}
      {!signals.isLoading && filtered.length === 0 && (
        <Card>
          <div className="flex flex-col items-center py-10 gap-3 text-muted">
            <AlertTriangle size={24} className="text-muted/40" />
            <span className="text-sm">No signals match this filter.</span>
            <button
              onClick={() => scan.mutate()}
              disabled={scan.isPending}
              className="text-xs px-3 py-1.5 rounded bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20"
            >
              Run Scan to generate signals
            </button>
          </div>
        </Card>
      )}

      {/* Signal grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((s) => <SignalCard key={s.id} s={s} />)}
      </div>
    </div>
  );
}
