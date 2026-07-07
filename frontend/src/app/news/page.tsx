"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Newspaper, TrendingUp, TrendingDown, Minus,
  RefreshCw, Search, AlertTriangle, FileText,
  BarChart2, Globe, Zap, ChevronRight, Building2,
  Users,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Sentiment {
  polarity: number;
  impact: number;
  magnitude: number;
  tag: "bullish" | "bearish" | "neutral";
}

interface NewsItem {
  id: string;
  headline: string;
  summary: string;
  source: string;
  url: string;
  symbols: string[];
  created_at: string;
  sentiment: Sentiment;
}

interface NewsFeed {
  symbols: string[] | null;
  lookback_hours: number;
  total: number;
  aggregate: {
    polarity: number;
    impact: number;
    magnitude: number;
    direction: string;
    top_headline: string;
    headline_count?: number;
  };
  items: NewsItem[];
}

interface WatchlistEntry {
  symbol: string;
  headline_count: number;
  polarity: number;
  impact: number;
  magnitude: number;
  direction: string;
  top_headline: string;
}

interface WatchlistNews {
  lookback_hours: number;
  symbols_with_news: number;
  items: WatchlistEntry[];
}

interface InsiderData {
  symbol: string;
  signal: {
    direction: string;
    confidence: number;
    reason: string;
    total_buy_value: number;
    total_sell_value: number;
    transaction_count: number;
  } | null;
  transactions: {
    insider_name: string;
    insider_title: string;
    transaction_type: string;
    shares: number;
    price_per_share: number;
    total_value: number;
    transaction_date: string;
    filing_url: string;
  }[];
  sec_8k_events: {
    company_name: string;
    filed_at: string;
    items: string[];
    description: string;
    filing_url: string;
  }[];
}

interface AnalystData {
  symbol: string;
  total: number;
  upgrades: { action: string; headline: string; firm: string; published_at: string }[];
  downgrades: { action: string; headline: string; firm: string; published_at: string }[];
  items: { action: string; headline: string; firm: string; price_target: number | null; published_at: string }[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function sentimentCls(tag: string) {
  if (tag === "bullish") return "text-bull bg-bull/10 border-bull/30";
  if (tag === "bearish") return "text-bear bg-bear/10 border-bear/30";
  return "text-muted bg-panel2 border-border";
}

function SentimentBadge({ tag }: { tag: string }) {
  const Icon = tag === "bullish" ? TrendingUp : tag === "bearish" ? TrendingDown : Minus;
  return (
    <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase", sentimentCls(tag))}>
      <Icon size={9} />{tag}
    </span>
  );
}

function timeAgo(iso: string) {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function fmt$(n: number) {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, icon: Icon, count, children, accent }: {
  title: string;
  icon: React.ElementType;
  count?: number;
  children: React.ReactNode;
  accent?: string;
}) {
  return (
    <div className="bg-panel border border-border rounded-lg overflow-hidden">
      <div className={cn("flex items-center gap-2 px-4 py-2.5 border-b border-border", accent ?? "")}>
        <Icon size={13} className="text-accent" />
        <span className="text-sm font-semibold">{title}</span>
        {count !== undefined && (
          <span className="ml-auto text-[10px] text-muted bg-panel2 border border-border px-2 py-0.5 rounded">
            {count}
          </span>
        )}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

// ── News card ─────────────────────────────────────────────────────────────────

function NewsCard({ item, compact }: { item: NewsItem; compact?: boolean }) {
  const { sentiment } = item;
  const border = sentiment.tag === "bullish" ? "border-l-bull/60"
    : sentiment.tag === "bearish" ? "border-l-bear/60" : "border-l-border";

  return (
    <div className={cn("border-l-2 pl-3 py-2", border)}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <SentimentBadge tag={sentiment.tag} />
          {sentiment.impact > 1.3 && (
            <span className="inline-flex items-center gap-1 text-[9px] px-1 py-0.5 rounded bg-yellow-400/10 border border-yellow-400/30 text-yellow-400">
              <Zap size={8} />HIGH
            </span>
          )}
          {item.symbols.slice(0, 3).map(s => (
            <span key={s} className="text-[9px] px-1.5 rounded bg-accent/10 border border-accent/20 text-accent font-mono">{s}</span>
          ))}
        </div>
        <span className="text-[10px] text-muted shrink-0">{timeAgo(item.created_at)}</span>
      </div>
      <a
        href={item.url || "#"}
        target="_blank"
        rel="noopener noreferrer"
        className={cn("block mt-1 font-medium hover:text-accent leading-snug", compact ? "text-xs line-clamp-2" : "text-sm line-clamp-3")}
      >
        {item.headline}
      </a>
      {!compact && item.summary && (
        <p className="text-xs text-muted mt-1 line-clamp-1">{item.summary}</p>
      )}
      <div className="flex items-center gap-2 mt-1 text-[10px] text-muted">
        <span className={sentiment.polarity > 0 ? "text-bull" : sentiment.polarity < 0 ? "text-bear" : ""}>
          {sentiment.polarity > 0 ? "+" : ""}{sentiment.polarity.toFixed(2)} pol
        </span>
        <span>·</span>
        <span>{sentiment.impact.toFixed(1)}x impact</span>
        {item.source && <><span>·</span><span className="opacity-60">{item.source}</span></>}
      </div>
    </div>
  );
}

// ── Watchlist heat row ────────────────────────────────────────────────────────

function WatchRow({ entry }: { entry: WatchlistEntry }) {
  const barPct = Math.min(100, entry.magnitude * 70);
  const barCol = entry.direction === "bullish" ? "bg-bull"
    : entry.direction === "bearish" ? "bg-bear" : "bg-muted/40";
  return (
    <div className="flex items-center gap-2 py-1.5">
      <span className="font-mono font-bold text-xs w-12 shrink-0">{entry.symbol}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <div className="h-1.5 flex-1 bg-panel2 rounded-full overflow-hidden">
            <div className={cn("h-full rounded-full transition-all", barCol)} style={{ width: `${barPct}%` }} />
          </div>
          <SentimentBadge tag={entry.direction} />
          <span className="text-[9px] text-muted shrink-0">{entry.headline_count}</span>
        </div>
        <p className="text-[10px] text-muted truncate">{entry.top_headline}</p>
      </div>
    </div>
  );
}

// ── Insider panel ─────────────────────────────────────────────────────────────

function InsiderPanel({ symbol }: { symbol: string }) {
  const q = useQuery({
    queryKey: ["insider", symbol],
    queryFn: () => api<InsiderData>(`/api/v1/news/insider/${symbol}`),
    staleTime: 300_000,
    enabled: !!symbol,
  });

  if (q.isLoading) return <p className="text-xs text-muted py-4 text-center">Loading SEC filings…</p>;
  if (q.isError) return (
    <p className="text-xs text-muted/60 py-3 text-center">SEC data unavailable</p>
  );

  const d = q.data;
  if (!d) return null;

  const hasSignal = d.signal && d.signal.transaction_count > 0;
  const hasTx = d.transactions.length > 0;
  const has8k = d.sec_8k_events.length > 0;

  if (!hasSignal && !hasTx && !has8k) {
    return <p className="text-xs text-muted py-4 text-center">No recent insider filings for {symbol}</p>;
  }

  return (
    <div className="space-y-3">
      {/* Signal summary */}
      {d.signal && (
        <div className={cn(
          "flex items-center gap-2 rounded p-2 border text-xs",
          d.signal.direction === "buy" ? "bg-bull/10 border-bull/30 text-bull"
            : d.signal.direction === "sell" ? "bg-bear/10 border-bear/30 text-bear"
            : "bg-panel2 border-border text-muted",
        )}>
          <FileText size={12} />
          <span className="font-semibold uppercase">{d.signal.direction === "buy" ? "Insider Buying" : d.signal.direction === "sell" ? "Insider Selling" : "Mixed Activity"}</span>
          <span className="opacity-70">{d.signal.transaction_count} filings</span>
          {d.signal.total_buy_value > 0 && <span className="ml-auto opacity-70">{fmt$(d.signal.total_buy_value)} bought</span>}
          <span className="opacity-60">{(d.signal.confidence * 100).toFixed(0)}% conf</span>
        </div>
      )}

      {/* Transactions */}
      {hasTx && (
        <div className="space-y-1">
          {d.transactions.slice(0, 5).map((t, i) => (
            <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-border/40 last:border-0">
              <span className={cn(
                "font-semibold w-4 text-center",
                t.transaction_type === "P" ? "text-bull" : t.transaction_type === "S" ? "text-bear" : "text-muted",
              )}>
                {t.transaction_type === "P" ? "B" : t.transaction_type === "S" ? "S" : "A"}
              </span>
              <span className="flex-1 truncate text-muted">{t.insider_name || "Insider"}</span>
              {t.total_value > 0 && <span className="font-mono">{fmt$(t.total_value)}</span>}
              <span className="text-[10px] text-muted/60">{timeAgo(t.transaction_date)}</span>
            </div>
          ))}
        </div>
      )}

      {/* 8-K events */}
      {has8k && (
        <div className="space-y-1 pt-1 border-t border-border">
          <p className="text-[10px] text-muted uppercase tracking-wide font-semibold">8-K Filings</p>
          {d.sec_8k_events.slice(0, 3).map((e, i) => (
            <div key={i} className="text-xs text-muted py-1">
              <span className="text-text">{e.company_name || symbol}</span>
              {e.items.length > 0 && <span className="ml-2 opacity-60">{e.items.join(", ")}</span>}
              <span className="ml-2 text-[10px] opacity-50">{timeAgo(e.filed_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Analyst panel ─────────────────────────────────────────────────────────────

function AnalystPanel({ symbol }: { symbol: string }) {
  const q = useQuery({
    queryKey: ["analyst", symbol],
    queryFn: () => api<AnalystData>(`/api/v1/news/analyst/${symbol}`),
    staleTime: 300_000,
    enabled: !!symbol,
  });

  if (q.isLoading) return <p className="text-xs text-muted py-4 text-center">Loading ratings…</p>;
  if (!q.data || q.data.total === 0) return (
    <p className="text-xs text-muted py-4 text-center">No recent analyst activity for {symbol}</p>
  );

  return (
    <div className="space-y-1">
      {q.data.items.slice(0, 6).map((r, i) => (
        <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/40 last:border-0">
          <span className={cn(
            "text-[10px] font-bold px-1.5 py-0.5 rounded border",
            r.action === "upgrade" ? "bg-bull/10 border-bull/30 text-bull"
              : r.action === "downgrade" ? "bg-bear/10 border-bear/30 text-bear"
              : "bg-panel2 border-border text-muted",
          )}>
            {r.action.toUpperCase()}
          </span>
          <span className="text-xs flex-1 truncate text-muted">{r.headline || `${r.firm || "Analyst"} ${r.action}`}</span>
          {r.price_target && <span className="text-xs font-mono text-text">${r.price_target}</span>}
          <span className="text-[10px] text-muted/60">{timeAgo(r.published_at)}</span>
        </div>
      ))}
    </div>
  );
}

// ── Aggregate tape bar ────────────────────────────────────────────────────────

function TapeBar({ agg, label }: { agg: NewsFeed["aggregate"]; label: string }) {
  const dir = agg?.direction ?? "neutral";
  const cls = dir === "bullish" ? "border-bull/30 bg-bull/5 text-bull"
    : dir === "bearish" ? "border-bear/30 bg-bear/5 text-bear"
    : "border-border bg-panel2 text-muted";
  const Icon = dir === "bullish" ? TrendingUp : dir === "bearish" ? TrendingDown : Minus;
  return (
    <div className={cn("flex items-center gap-3 px-3 py-2 rounded border text-xs", cls)}>
      <Icon size={13} />
      <span className="font-semibold capitalize">{dir}</span>
      <span className="text-[10px] opacity-70">{label}</span>
      <span className="ml-auto opacity-70">
        {agg.polarity > 0 ? "+" : ""}{agg.polarity.toFixed(2)} · {(agg.headline_count ?? 0)} headlines
      </span>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

const HOURS = [4, 8, 24, 48] as const;

export default function NewsPage() {
  const [hours, setHours] = useState(8);
  const [symbolInput, setSymbolInput] = useState("NVDA");
  const [activeSymbol, setActiveSymbol] = useState("NVDA");
  const [sentFilter, setSentFilter] = useState("all");

  // Market-wide news
  const marketQ = useQuery({
    queryKey: ["news-market", hours],
    queryFn: () => api<NewsFeed>(`/api/v1/news/market?lookback_hours=${hours}&limit=40`),
    refetchInterval: 90_000,
    staleTime: 60_000,
  });

  // Per-symbol news
  const symQ = useQuery({
    queryKey: ["news-sym", activeSymbol, hours],
    queryFn: () => api<NewsFeed>(`/api/v1/news/feed?symbols=${activeSymbol}&lookback_hours=${hours}&limit=30`),
    refetchInterval: 90_000,
    staleTime: 60_000,
    enabled: !!activeSymbol,
  });

  // Watchlist heat map
  const watchQ = useQuery({
    queryKey: ["news-watch", hours],
    queryFn: () => api<WatchlistNews>(`/api/v1/news/watchlist?lookback_hours=${hours}`),
    refetchInterval: 120_000,
    staleTime: 90_000,
  });

  const isFetching = marketQ.isFetching || watchQ.isFetching;

  const marketItems = (marketQ.data?.items ?? []).filter(
    it => sentFilter === "all" || it.sentiment.tag === sentFilter
  );
  const symItems = (symQ.data?.items ?? []).filter(
    it => sentFilter === "all" || it.sentiment.tag === sentFilter
  );

  return (
    <div className="space-y-4">

      {/* ── Header ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Newspaper size={17} className="text-accent" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight">News & Intelligence</h1>
            <p className="text-xs text-muted">Live headlines · SEC filings · analyst ratings · insider activity</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <div className="flex rounded border border-border bg-panel2 overflow-hidden">
            {HOURS.map(h => (
              <button key={h} onClick={() => setHours(h)}
                className={cn("px-2.5 py-1 text-xs transition-colors", hours === h ? "bg-accent text-bg font-semibold" : "text-muted hover:text-text")}>
                {h}h
              </button>
            ))}
          </div>
          <button onClick={() => { marketQ.refetch(); watchQ.refetch(); symQ.refetch(); }}
            disabled={isFetching}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-border bg-panel2 text-muted hover:text-text disabled:opacity-50">
            <RefreshCw size={11} className={cn(isFetching && "animate-spin")} />Refresh
          </button>
        </div>
      </div>

      {/* ── Sentiment filter ── */}
      <div className="flex items-center gap-1 w-fit rounded border border-border bg-panel2 p-0.5">
        {["all","bullish","bearish","neutral"].map(f => (
          <button key={f} onClick={() => setSentFilter(f)}
            className={cn("px-2.5 py-1 rounded text-xs capitalize transition-colors",
              sentFilter === f
                ? f === "bullish" ? "bg-bull/20 text-bull font-semibold"
                  : f === "bearish" ? "bg-bear/20 text-bear font-semibold"
                  : "bg-accent/20 text-accent font-semibold"
                : "text-muted hover:text-text")}>
            {f}
          </button>
        ))}
      </div>

      {/* ── Main 2-column layout ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">

        {/* Left col — market news feed */}
        <div className="xl:col-span-2 space-y-4">

          {/* Market tape aggregate */}
          {marketQ.data && <TapeBar agg={marketQ.data.aggregate} label={`Market · last ${hours}h`} />}

          {/* Market news section */}
          <Section title="Market News" icon={Globe} count={marketItems.length}>
            {marketQ.isLoading && <Skeleton rows={5} />}
            {marketQ.isError && <ErrorRow error={marketQ.error as Error} />}
            {!marketQ.isLoading && marketItems.length === 0 && (
              <Empty label="No market headlines in this window — try a longer lookback" />
            )}
            <div className="space-y-1 divide-y divide-border/40">
              {marketItems.slice(0, 20).map(it => (
                <div key={it.id} className="pt-2 first:pt-0">
                  <NewsCard item={it} compact />
                </div>
              ))}
            </div>
          </Section>

          {/* Symbol news section */}
          <Section title={`${activeSymbol} News`} icon={Newspaper} count={symItems.length}>
            {symQ.isLoading && <Skeleton rows={3} />}
            {!symQ.isLoading && symItems.length === 0 && (
              <Empty label={`No news for ${activeSymbol} in the last ${hours}h`} />
            )}
            {symQ.data && <TapeBar agg={symQ.data.aggregate} label={activeSymbol} />}
            <div className="mt-3 space-y-1 divide-y divide-border/40">
              {symItems.slice(0, 10).map(it => (
                <div key={it.id} className="pt-2 first:pt-0">
                  <NewsCard item={it} />
                </div>
              ))}
            </div>
          </Section>
        </div>

        {/* Right col — symbol search + insider + analyst + watchlist */}
        <div className="space-y-4">

          {/* Symbol search */}
          <div className="bg-panel border border-border rounded-lg p-3">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">Symbol lookup</p>
            <form onSubmit={e => { e.preventDefault(); setActiveSymbol(symbolInput.toUpperCase().trim()); }}
              className="flex gap-2">
              <div className="relative flex-1">
                <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
                <input value={symbolInput} onChange={e => setSymbolInput(e.target.value.toUpperCase())}
                  placeholder="AAPL, TSLA…"
                  className="w-full pl-7 pr-2 py-1.5 text-sm rounded border border-border bg-panel2 text-text placeholder:text-muted focus:outline-none focus:border-accent" />
              </div>
              <button type="submit"
                className="px-3 py-1.5 text-xs rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 font-medium">
                Go
              </button>
            </form>
          </div>

          {/* SEC insider filings */}
          <Section title="SEC Insider Filings" icon={FileText}>
            <p className="text-[10px] text-muted mb-3 leading-relaxed">
              Insiders must publicly disclose trades within 2 business days (Form 4).
              Cluster buys = high-conviction signal.
            </p>
            <InsiderPanel symbol={activeSymbol} />
          </Section>

          {/* Analyst ratings */}
          <Section title="Analyst Ratings" icon={Users}>
            <p className="text-[10px] text-muted mb-3">Recent upgrades, downgrades &amp; initiations for {activeSymbol}</p>
            <AnalystPanel symbol={activeSymbol} />
          </Section>

          {/* Watchlist heat map */}
          <Section title="Watchlist Sentiment" icon={BarChart2} count={watchQ.data?.symbols_with_news}>
            {watchQ.isLoading && <Skeleton rows={6} />}
            {watchQ.isError && <ErrorRow error={watchQ.error as Error} />}
            {!watchQ.isLoading && (watchQ.data?.items ?? []).length === 0 && (
              <Empty label={`No watchlist news in the last ${hours}h`} />
            )}
            <div className="divide-y divide-border/40">
              {(watchQ.data?.items ?? []).slice(0, 15).map(e => (
                <WatchRow key={e.symbol} entry={e} />
              ))}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

// ── Utility micro-components ──────────────────────────────────────────────────

function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 py-1">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 rounded bg-panel2 animate-pulse" />
      ))}
    </div>
  );
}

function Empty({ label }: { label: string }) {
  return <p className="text-xs text-muted py-4 text-center">{label}</p>;
}

function ErrorRow({ error }: { error: Error }) {
  return (
    <div className="flex items-center gap-2 text-xs text-bear py-2">
      <AlertTriangle size={12} />
      <span className="line-clamp-2">{error.message}</span>
    </div>
  );
}
