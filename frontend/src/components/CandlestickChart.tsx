"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"] as const;
type TF = (typeof TIMEFRAMES)[number];

const OVERLAYS = ["SMA20", "SMA50", "VWAP"] as const;
type Overlay = (typeof OVERLAYS)[number];

function sma(bars: Bar[], period: number): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1);
    const avg = slice.reduce((s, b) => s + b.close, 0) / period;
    out.push({ time: bars[i].time, value: +avg.toFixed(4) });
  }
  return out;
}

function vwapLine(bars: Bar[]): { time: number; value: number }[] {
  let cumTPV = 0;
  let cumVol = 0;
  return bars.map((b) => {
    const tp = (b.high + b.low + b.close) / 3;
    cumTPV += tp * b.volume;
    cumVol += b.volume;
    return { time: b.time, value: cumVol > 0 ? +(cumTPV / cumVol).toFixed(4) : b.close };
  });
}

export function CandlestickChart({
  initialSymbol = "AAPL",
  height = 420,
}: {
  initialSymbol?: string;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<ReturnType<typeof import("lightweight-charts")["createChart"]> | null>(null);
  const candleRef    = useRef<any>(null);
  const volRef       = useRef<any>(null);
  const sma20Ref     = useRef<any>(null);
  const sma50Ref     = useRef<any>(null);
  const vwapRef      = useRef<any>(null);

  const [symbol, setSymbol]   = useState(initialSymbol);
  const [input, setInput]     = useState(initialSymbol);
  const [tf, setTf]           = useState<TF>("1d");
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(["SMA20"]));
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [ohlcv, setOhlcv]     = useState<{ o: number; h: number; l: number; c: number; v: number } | null>(null);

  const toggleOverlay = (o: Overlay) => {
    setOverlays((prev) => {
      const next = new Set(prev);
      next.has(o) ? next.delete(o) : next.add(o);
      return next;
    });
  };

  const loadChart = useCallback(async (sym: string, timeframe: TF) => {
    if (!containerRef.current) return;
    setLoading(true);
    setError(null);
    try {
      const bars: Bar[] = await api<Bar[]>(`/v1/bars/${sym}?timeframe=${timeframe}&limit=500`);
      if (!bars.length) { setError("No data returned"); return; }

      const {
        createChart, CrosshairMode,
        CandlestickSeries, HistogramSeries, LineSeries,
      } = await import("lightweight-charts");

      if (!chartRef.current) {
        chartRef.current = createChart(containerRef.current, {
          layout: {
            background: { color: "#13171c" },
            textColor:  "#7a8595",
          },
          grid: {
            vertLines: { color: "#1a1f26" },
            horzLines: { color: "#1a1f26" },
          },
          crosshair: { mode: CrosshairMode.Normal },
          rightPriceScale: { borderColor: "#252b34" },
          timeScale: {
            borderColor:     "#252b34",
            timeVisible:     true,
            secondsVisible:  false,
          },
          width:  containerRef.current.clientWidth,
          height: height - 60,
        });

        // Candles (v5 API)
        candleRef.current = chartRef.current.addSeries(CandlestickSeries, {
          upColor:          "#22c55e",
          downColor:        "#ef4444",
          borderUpColor:    "#22c55e",
          borderDownColor:  "#ef4444",
          wickUpColor:      "#22c55e",
          wickDownColor:    "#ef4444",
        });

        // Volume histogram (bottom 15%)
        volRef.current = chartRef.current.addSeries(HistogramSeries, {
          color:       "#3b82f620",
          priceFormat: { type: "volume" },
          priceScaleId: "vol",
        });
        chartRef.current.priceScale("vol").applyOptions({
          scaleMargins: { top: 0.85, bottom: 0 },
        });

        // SMA 20
        sma20Ref.current = chartRef.current.addSeries(LineSeries, {
          color:       "#3b82f6",
          lineWidth:   1,
          priceLineVisible: false,
        });
        // SMA 50
        sma50Ref.current = chartRef.current.addSeries(LineSeries, {
          color:       "#f97316",
          lineWidth:   1,
          priceLineVisible: false,
        });
        // VWAP
        vwapRef.current = chartRef.current.addSeries(LineSeries, {
          color:       "#a855f7",
          lineWidth:   1,
          priceLineVisible: false,
        });

        // Crosshair tooltip
        chartRef.current.subscribeCrosshairMove((param) => {
          if (!param.time || !param.seriesData) { setOhlcv(null); return; }
          const d = param.seriesData.get(candleRef.current) as any;
          if (d) setOhlcv({ o: d.open, h: d.high, l: d.low, c: d.close, v: 0 });
        });

        // Resize observer
        const ro = new ResizeObserver(() => {
          if (containerRef.current && chartRef.current) {
            chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
          }
        });
        ro.observe(containerRef.current);
      }

      // Set data
      const sorted = [...bars].sort((a, b) => a.time - b.time);
      candleRef.current.setData(sorted);
      volRef.current.setData(sorted.map((b) => ({
        time: b.time,
        value: b.volume,
        color: b.close >= b.open ? "#22c55e30" : "#ef444430",
      })));

      sma20Ref.current.setData(sma(sorted, 20));
      sma50Ref.current.setData(sma(sorted, 50));
      vwapRef.current.setData(vwapLine(sorted));

      chartRef.current.timeScale().fitContent();
    } catch (e: any) {
      setError(e?.message ?? "Failed to load chart");
    } finally {
      setLoading(false);
    }
  }, [height]);

  // Toggle overlay visibility
  useEffect(() => {
    if (!sma20Ref.current) return;
    sma20Ref.current.applyOptions({ visible: overlays.has("SMA20") });
    sma50Ref.current?.applyOptions({ visible: overlays.has("SMA50") });
    vwapRef.current?.applyOptions({ visible: overlays.has("VWAP") });
  }, [overlays]);

  // Load on symbol / tf change
  useEffect(() => {
    loadChart(symbol, tf);
  }, [symbol, tf, loadChart]);

  // Destroy chart on unmount
  useEffect(() => {
    return () => {
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, []);

  const fmt = (n: number) => n?.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="flex flex-col gap-2 h-full">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Symbol input */}
        <form
          onSubmit={(e) => { e.preventDefault(); const s = input.trim().toUpperCase(); if (s) setSymbol(s); }}
          className="flex"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            className="w-24 bg-panel2 border border-border rounded-l px-2 py-1 text-sm font-mono text-text focus:outline-none focus:border-accent"
            placeholder="AAPL"
          />
          <button
            type="submit"
            className="bg-accent/20 border border-border border-l-0 rounded-r px-2 py-1 text-xs text-accent hover:bg-accent/30"
          >
            Go
          </button>
        </form>

        {/* Timeframe selector */}
        <div className="flex rounded border border-border overflow-hidden">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={cn(
                "px-2 py-1 text-xs font-mono",
                tf === t
                  ? "bg-accent text-bg font-semibold"
                  : "bg-panel2 text-muted hover:text-text",
              )}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Overlay toggles */}
        <div className="flex gap-1 ml-auto">
          {OVERLAYS.map((o) => (
            <button
              key={o}
              onClick={() => toggleOverlay(o)}
              className={cn(
                "px-2 py-1 text-[10px] rounded border font-mono",
                overlays.has(o)
                  ? o === "SMA20" ? "border-blue-500 text-blue-400 bg-blue-500/10"
                    : o === "SMA50" ? "border-orange-500 text-orange-400 bg-orange-500/10"
                    : "border-purple-500 text-purple-400 bg-purple-500/10"
                  : "border-border text-muted bg-panel2",
              )}
            >
              {o}
            </button>
          ))}
        </div>
      </div>

      {/* OHLCV tooltip */}
      {ohlcv && (
        <div className="flex gap-3 text-[11px] font-mono text-muted">
          <span>O <span className="text-text">{fmt(ohlcv.o)}</span></span>
          <span>H <span className="text-bull">{fmt(ohlcv.h)}</span></span>
          <span>L <span className="text-bear">{fmt(ohlcv.l)}</span></span>
          <span>C <span className={ohlcv.c >= ohlcv.o ? "text-bull" : "text-bear"}>{fmt(ohlcv.c)}</span></span>
        </div>
      )}

      {/* Chart container */}
      <div className="relative flex-1" style={{ minHeight: height - 60 }}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-panel/80 z-10 rounded">
            <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-bear text-sm">
            {error}
          </div>
        )}
        <div ref={containerRef} className="w-full h-full" />
      </div>
    </div>
  );
}
