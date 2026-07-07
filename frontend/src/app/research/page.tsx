"use client";
import { useState, useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card } from "@/components/primitives";
import { cn } from "@/lib/utils";
import {
  TrendingUp, TrendingDown, RefreshCw, Users,
  AlertTriangle, Zap, Star, Activity, BarChart2,
  Copy, Check,
} from "lucide-react";

interface Analyst {
  id: string;
  name: string;
  role: string;
  specialty: string;
  avatar: string;
}

interface AnalystNote {
  analyst: Analyst;
  timestamp: string;
  headline: string;
  body: string;
  tags: string[];
  priority: "high" | "medium" | "low";
}

interface StockFocus {
  symbol: string;
  price: number;
  change_pct: number;
  trend: string;
  rsi: number;
  rel_volume: number;
  setup: string;
  atr: number;
  grade?: string;
  action?: string;
  score?: number;
}

interface ResearchBrief {
  generated_at: string;
  market_regime: string;
  regime_color: string;
  regime_summary: string;
  spy_price: number;
  spy_change_pct: number;
  analyst_notes: AnalystNote[];
  stock_focus: StockFocus[];
  active_analysts: number;
  top_setups: (StockFocus & { grade: string; action: string; score: number })[];
}

interface Signal {
  id: string;
  strategy: string;
  asset_class: "stock" | "option";
  symbol: string;
  underlying?: string | null;
  direction: "bullish" | "bearish" | "neutral";
  entry: number;
  stop_loss: number;
  take_profit: number;
  confidence: number;
  reason: string;
  risk_reward?: number | null;
  suggested_qty: number;
  status: string;
  generated_at: string;
  metadata?: Record<string, unknown>;
}

function signalScore(s: Signal) {
  const rr = Math.min((s.risk_reward ?? 1.5) / 4, 1);
  const optionBoost = s.asset_class === "option" ? 0.08 : 0;
  const freshMs = Date.now() - new Date(s.generated_at).getTime();
  const freshBoost = freshMs < 20 * 60_000 ? 0.08 : freshMs < 60 * 60_000 ? 0.04 : 0;
  return s.confidence * 0.62 + rr * 0.3 + optionBoost + freshBoost;
}

function signalGrade(s: Signal) {
  const score = signalScore(s);
  if (score >= 0.82 && s.confidence >= 0.72) return "A+";
  if (score >= 0.68 && s.confidence >= 0.6) return "A";
  if (score >= 0.54) return "B";
  return "C";
}

function gradeClass(grade: string) {
  if (grade === "A+") return "text-emerald-400 bg-emerald-400/10 border-emerald-400/30";
  if (grade === "A") return "text-bull bg-bull/10 border-bull/30";
  if (grade === "B") return "text-yellow-400 bg-yellow-400/10 border-yellow-400/30";
  return "text-muted bg-panel2 border-border";
}

function buildCopyTrade(s: Signal) {
  const qty = s.suggested_qty ?? 1;
  const isOption = s.asset_class === "option";
  const bull = s.direction === "bullish";
  const fmt = (n: number) => n.toFixed(2);
  const rr = s.risk_reward ? `R:R ${s.risk_reward.toFixed(1)}:1` : "R:R n/a";

  if (isOption) {
    const opt = s.metadata?.selected_option as Record<string, unknown> | undefined;
    const contract = (opt?.symbol as string | undefined) ?? s.symbol;
    const right = ((opt?.right as string | undefined) ?? (bull ? "CALL" : "PUT")).toUpperCase();
    const strike = opt?.strike ? `$${opt.strike}` : "";
    const exp = opt?.expiration
      ? new Date(opt.expiration as string).toLocaleDateString("en-US", {
          month: "short", day: "numeric", year: "2-digit",
        })
      : "";
    return [
      "COPY TRADE - OPTIONS",
      "Action: BUY TO OPEN",
      `Contract: ${contract}`,
      `Type: ${right} ${strike} ${exp}`,
      `Qty: ${qty}`,
      `Limit: $${fmt(s.entry)}`,
      `Stop: $${fmt(s.stop_loss)}`,
      `Target: $${fmt(s.take_profit)} (${rr})`,
      `Strategy: ${s.strategy}`,
      `Confidence: ${(s.confidence * 100).toFixed(0)}% Grade ${signalGrade(s)}`,
      `Reason: ${s.reason.slice(0, 180)}`,
    ].join("\n");
  }

  return [
    "COPY TRADE - STOCK",
    `Action: ${bull ? "BUY" : "WATCH PUTS / AVOID LONG"}`,
    `Symbol: ${s.symbol}`,
    `Qty: ${qty}`,
    `Limit: $${fmt(s.entry)}`,
    `Stop: $${fmt(s.stop_loss)}`,
    `Target: $${fmt(s.take_profit)} (${rr})`,
    `Strategy: ${s.strategy}`,
    `Confidence: ${(s.confidence * 100).toFixed(0)}% Grade ${signalGrade(s)}`,
    `Reason: ${s.reason.slice(0, 180)}`,
  ].join("\n");
}

function RegimeBadge({ regime, color }: { regime: string; color: string }) {
  const colors: Record<string, string> = {
    green: "text-bull bg-bull/10 border-bull/30",
    red: "text-bear bg-bear/10 border-bear/30",
    yellow: "text-yellow-400 bg-yellow-400/10 border-yellow-400/30",
    orange: "text-orange-400 bg-orange-400/10 border-orange-400/30",
    gray: "text-muted bg-panel2 border-border",
  };
  return (
    <span className={cn("px-2 py-1 rounded border text-xs font-semibold uppercase tracking-wide", colors[color] ?? colors.gray)}>
      {regime}
    </span>
  );
}

function PriorityIcon({ priority }: { priority: string }) {
  if (priority === "high") return <AlertTriangle size={12} className="text-bear" />;
  if (priority === "medium") return <Zap size={12} className="text-yellow-400" />;
  return <Activity size={12} className="text-muted" />;
}

function AnalystNote({ note }: { note: AnalystNote }) {
  const priorityBorder: Record<string, string> = {
    high: "border-l-bear/60",
    medium: "border-l-yellow-400/60",
    low: "border-l-border",
  };
  return (
    <div className={cn(
      "bg-panel border border-border rounded-lg p-4 border-l-2",
      priorityBorder[note.priority],
    )}>
      <div className="flex items-start gap-3">
        <div className={cn(
          "w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0",
          "bg-accent/20 text-accent border border-accent/30",
        )}>
          {note.analyst.avatar}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div>
              <span className="text-xs font-semibold text-text">{note.analyst.name}</span>
              <span className="text-[10px] text-muted ml-2">{note.analyst.role}</span>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <PriorityIcon priority={note.priority} />
              <span className="text-[10px] text-muted">
                {new Date(note.timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          </div>
          <p className="text-sm font-semibold text-text mt-1">{note.headline}</p>
          <p className="text-xs text-muted leading-relaxed mt-1 whitespace-pre-wrap">{note.body}</p>
          <div className="flex flex-wrap gap-1 mt-2">
            {note.tags.map((t) => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-panel2 border border-border text-muted">
                {t}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SetupCard({ s }: { s: StockFocus & { grade: string; action: string; score: number } }) {
  const bull = s.trend === "bullish";
  const gradeColors: Record<string, string> = {
    "A+": "text-emerald-400 bg-emerald-400/10 border-emerald-400/30",
    "A":  "text-bull bg-bull/10 border-bull/30",
    "B":  "text-yellow-400 bg-yellow-400/10 border-yellow-400/30",
    "C":  "text-muted bg-panel2 border-border",
  };
  return (
    <div className={cn(
      "bg-panel border rounded-lg p-3 flex items-center gap-3",
      bull ? "border-bull/20" : "border-bear/20",
    )}>
      <div className="flex flex-col items-center gap-1">
        {bull ? <TrendingUp size={18} className="text-bull" /> : <TrendingDown size={18} className="text-bear" />}
        <span className={cn("text-[10px] font-bold px-1.5 py-0.5 rounded border", gradeColors[s.grade] ?? gradeColors.C)}>
          {s.grade}
        </span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="font-mono font-bold">{s.symbol}</span>
          <span className={cn("text-xs font-mono font-semibold", s.change_pct >= 0 ? "text-bull" : "text-bear")}>
            {s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(2)}%
          </span>
        </div>
        <div className="text-xs text-muted">{s.setup.replace(/_/g, " ")}</div>
        <div className="flex items-center gap-3 mt-1 text-[10px] text-muted">
          <span>RSI {s.rsi}</span>
          <span>Vol {s.rel_volume}x</span>
          <span className={cn(
            "px-1.5 py-0.5 rounded font-semibold",
            s.action === "BUY" ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear",
          )}>
            {s.action}
          </span>
        </div>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }, [text]);

  return (
    <button
      onClick={onCopy}
      className={cn(
        "inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] font-medium",
        copied
          ? "border-bull/40 bg-bull/15 text-bull"
          : "border-accent/30 bg-accent/10 text-accent hover:bg-accent/20",
      )}
    >
      {copied ? <Check size={10} /> : <Copy size={10} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function ResearchSignalCard({ s }: { s: Signal }) {
  const bull = s.direction === "bullish";
  const isOption = s.asset_class === "option";
  const grade = signalGrade(s);
  const opt = s.metadata?.selected_option as Record<string, unknown> | undefined;
  const title = isOption
    ? `${s.underlying ?? s.symbol.slice(0, 4)} ${((opt?.right as string | undefined) ?? (bull ? "CALL" : "PUT")).toUpperCase()}`
    : s.symbol;
  const ageMin = Math.max(0, Math.round((Date.now() - new Date(s.generated_at).getTime()) / 60_000));

  return (
    <div className={cn(
      "rounded-lg border bg-panel p-3",
      bull ? "border-bull/25" : "border-bear/25",
    )}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          {bull ? <TrendingUp size={18} className="text-bull" /> : <TrendingDown size={18} className="text-bear" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-bold">{title}</span>
            {isOption && <span className="rounded border border-accent/25 bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">OPTION</span>}
            <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-bold", gradeClass(grade))}>{grade}</span>
            <span className="ml-auto text-[10px] text-muted">{ageMin < 1 ? "now" : `${ageMin}m`}</span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
            <div className="rounded bg-panel2 px-2 py-1">
              <div className="text-muted">Entry</div>
              <div className="font-mono text-text">${s.entry.toFixed(2)}</div>
            </div>
            <div className="rounded bg-bear/10 px-2 py-1">
              <div className="text-muted">Stop</div>
              <div className="font-mono text-bear">${s.stop_loss.toFixed(2)}</div>
            </div>
            <div className="rounded bg-bull/10 px-2 py-1">
              <div className="text-muted">Target</div>
              <div className="font-mono text-bull">${s.take_profit.toFixed(2)}</div>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
            <span>{s.strategy.replace(/_/g, " ")}</span>
            <span>{(s.confidence * 100).toFixed(0)}% conf</span>
            {s.risk_reward && <span>R:R {s.risk_reward.toFixed(1)}</span>}
            <span>{s.status}</span>
            <CopyButton text={buildCopyTrade(s)} />
          </div>
          <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-muted">{s.reason}</p>
        </div>
      </div>
    </div>
  );
}

export default function ResearchPage() {
  const qc = useQueryClient();
  const brief = useQuery({
    queryKey: ["research-brief"],
    queryFn: () => api<ResearchBrief>("/research/brief"),
    refetchInterval: 60_000,
    staleTime: 20_000,
  });

  const signals = useQuery({
    queryKey: ["research-signals"],
    queryFn: () => api<Signal[]>("/signals?limit=120"),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  const scanNow = useMutation({
    mutationFn: () => api<{ count: number; signals: Signal[] }>("/signals/scan", {
      method: "POST",
      body: JSON.stringify({ include_options: true }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research-signals"] });
      qc.invalidateQueries({ queryKey: ["signals-full"] });
      qc.invalidateQueries({ queryKey: ["research-brief"] });
    },
  });

  const data = brief.data;
  const bestSignals = (signals.data ?? [])
    .filter((s) => s.status !== "rejected")
    .sort((a, b) => signalScore(b) - signalScore(a))
    .slice(0, 8);
  const bestOptions = bestSignals.filter((s) => s.asset_class === "option").slice(0, 4);
  const bestStocks = bestSignals.filter((s) => s.asset_class === "stock").slice(0, 4);
  const now = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  const loading = brief.isLoading || signals.isLoading;
  const fetching = brief.isFetching || signals.isFetching || scanNow.isPending;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Research Desk</h1>
          <p className="text-xs text-muted">
            {data?.active_analysts ?? 10} analysts · Live as of {now}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {data && (
            <RegimeBadge regime={data.market_regime} color={data.regime_color} />
          )}
          <button
            onClick={() => {
              brief.refetch();
              signals.refetch();
            }}
            disabled={fetching}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-border bg-panel2 text-muted hover:text-text"
          >
            <RefreshCw size={11} className={cn(fetching && "animate-spin")} />
            Refresh
          </button>
          <button
            onClick={() => scanNow.mutate()}
            disabled={scanNow.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            <Zap size={11} />
            Scan now
          </button>
        </div>
      </div>

      {loading && (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 bg-panel border border-border rounded-lg animate-pulse" />
          ))}
        </div>
      )}

      {(brief.isError || signals.isError) && (
        <div className="rounded-lg border border-bear/30 bg-bear/10 p-4 text-sm text-bear">
          Research feed error: {((brief.error ?? signals.error) as Error)?.message}
        </div>
      )}

      {!loading && bestSignals.length > 0 && (
        <div className="rounded-lg border border-accent/20 bg-accent/5 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Star size={15} className="text-yellow-400" />
            <span className="text-sm font-semibold">Copy-Ready Signals</span>
            <span className="rounded bg-panel2 px-2 py-0.5 text-[10px] text-muted">
              {bestStocks.length} stocks · {bestOptions.length} options
            </span>
            <span className="ml-auto text-[10px] text-muted">updates every 30s</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {bestSignals.map((s) => (
              <ResearchSignalCard key={s.id} s={s} />
            ))}
          </div>
        </div>
      )}

      {!loading && bestSignals.length === 0 && !signals.isError && (
        <div className="rounded-lg border border-yellow-400/25 bg-yellow-400/10 p-4 text-sm text-yellow-200">
          No copy-ready signals yet. The scanner is running; use Scan now to force a fresh pass.
        </div>
      )}

      {data && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {/* Left column: analyst notes */}
          <div className="xl:col-span-2 space-y-4">
            {/* Regime summary */}
            <div className={cn(
              "rounded-lg p-4 border text-sm",
              data.regime_color === "green" ? "bg-bull/5 border-bull/20" :
              data.regime_color === "red"   ? "bg-bear/5 border-bear/20" :
              "bg-panel border-border",
            )}>
              <div className="flex items-center gap-2 mb-1">
                <BarChart2 size={14} className="text-accent" />
                <span className="font-semibold text-xs uppercase tracking-wide text-accent">Market Regime</span>
                <span className="text-xs text-muted ml-auto">
                  SPY ${data.spy_price.toFixed(2)}
                  <span className={cn("ml-2 font-mono", data.spy_change_pct >= 0 ? "text-bull" : "text-bear")}>
                    {data.spy_change_pct >= 0 ? "+" : ""}{data.spy_change_pct.toFixed(2)}%
                  </span>
                </span>
              </div>
              <p className="text-xs text-muted">{data.regime_summary}</p>
            </div>

            {/* Analyst notes */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Users size={14} className="text-muted" />
                <span className="text-sm font-semibold">Analyst Feed</span>
                <span className="text-[10px] text-muted bg-panel2 border border-border px-2 py-0.5 rounded ml-auto">
                  {data.analyst_notes.length} notes
                </span>
              </div>
              {data.analyst_notes.map((note, i) => (
                <AnalystNote key={`${note.analyst.id}-${i}`} note={note} />
              ))}
            </div>
          </div>

          {/* Right column: top setups + watch list */}
          <div className="space-y-4">
            {/* Top setups */}
            {data.top_setups.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <Star size={14} className="text-yellow-400" />
                  <span className="text-sm font-semibold">Top Setups</span>
                  <span className="text-[10px] text-muted ml-auto">Copy to broker</span>
                </div>
                <div className="space-y-2">
                  {data.top_setups.map((s) => (
                    <SetupCard key={s.symbol} s={s} />
                  ))}
                </div>
              </div>
            )}

            {/* Focus list */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Activity size={14} className="text-accent" />
                <span className="text-sm font-semibold">Focus List</span>
              </div>
              <div className="space-y-1">
                {data.stock_focus.map((s) => (
                  <div key={s.symbol} className="flex items-center gap-2 px-3 py-2 rounded bg-panel border border-border text-xs">
                    <span className="font-mono font-bold w-12">{s.symbol}</span>
                    <span className={cn("font-mono w-14", s.change_pct >= 0 ? "text-bull" : "text-bear")}>
                      {s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(2)}%
                    </span>
                    <span className="text-muted text-[10px] flex-1 truncate">{s.setup.replace(/_/g, " ")}</span>
                    <span className="text-[10px] text-muted">Vol {s.rel_volume.toFixed(1)}x</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Analyst team */}
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <Users size={13} className="text-muted" />
                <span className="text-xs font-semibold">Research Team</span>
                <span className="ml-auto flex items-center gap-1 text-[10px] text-bull">
                  <span className="w-1.5 h-1.5 rounded-full bg-bull animate-pulse" />
                  All online
                </span>
              </div>
              <div className="space-y-1.5">
                {data.analyst_notes.map((n) => (
                  <div key={n.analyst.id} className="flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-accent/20 border border-accent/30 text-[9px] font-bold text-accent flex items-center justify-center">
                      {n.analyst.avatar}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-[10px] font-medium text-text truncate">{n.analyst.name}</div>
                      <div className="text-[9px] text-muted truncate">{n.analyst.role}</div>
                    </div>
                    <span className="w-1.5 h-1.5 rounded-full bg-bull" />
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
