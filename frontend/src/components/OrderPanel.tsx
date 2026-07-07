"use client";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CheckCircle, XCircle, AlertTriangle } from "lucide-react";

type OrderType = "market" | "limit" | "stop" | "stop_limit";
type TIF = "day" | "gtc" | "ioc";

interface PlaceOrderReq {
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  order_type: OrderType;
  limit_price?: number;
  stop_price?: number;
  time_in_force: TIF;
}

const ORDER_TYPES: { key: OrderType; label: string }[] = [
  { key: "market",     label: "Market"     },
  { key: "limit",      label: "Limit"      },
  { key: "stop",       label: "Stop"       },
  { key: "stop_limit", label: "Stop-Limit" },
];

export function OrderPanel({ defaultSymbol = "AAPL" }: { defaultSymbol?: string }) {
  const qc = useQueryClient();

  const [symbol,     setSymbol]     = useState(defaultSymbol);
  const [side,       setSide]       = useState<"buy" | "sell">("buy");
  const [qty,        setQty]        = useState<string>("1");
  const [orderType,  setOrderType]  = useState<OrderType>("market");
  const [limitPrice, setLimitPrice] = useState<string>("");
  const [stopPrice,  setStopPrice]  = useState<string>("");
  const [tif,        setTif]        = useState<TIF>("day");
  const [confirm,    setConfirm]    = useState(false);
  const [toast,      setToast]      = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  const showToast = (type: "ok" | "err", msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 4000);
  };

  const mutation = useMutation({
    mutationFn: (body: PlaceOrderReq) =>
      api<{ id: string; status: string }>("/v1/orders", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      showToast("ok", `Order placed — ID: ${data.id} (${data.status})`);
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      setConfirm(false);
    },
    onError: (e: any) => {
      showToast("err", e?.message ?? "Order failed");
      setConfirm(false);
    },
  });

  const needsLimit = orderType === "limit" || orderType === "stop_limit";
  const needsStop  = orderType === "stop"  || orderType === "stop_limit";

  const buildReq = (): PlaceOrderReq => ({
    symbol:      symbol.toUpperCase(),
    side,
    qty:         parseInt(qty) || 1,
    order_type:  orderType,
    limit_price: needsLimit && limitPrice ? parseFloat(limitPrice) : undefined,
    stop_price:  needsStop  && stopPrice  ? parseFloat(stopPrice)  : undefined,
    time_in_force: tif,
  });

  const preview = () => {
    const q = parseInt(qty) || 1;
    const lp = parseFloat(limitPrice);
    const price = needsLimit && lp ? `@ $${lp.toFixed(2)}` : "@ Market";
    return `${side.toUpperCase()} ${q} ${symbol.toUpperCase() || "—"} ${price}`;
  };

  return (
    <div className="flex flex-col h-full relative">
      {/* Toast */}
      {toast && (
        <div className={cn(
          "absolute top-2 left-2 right-2 z-20 flex items-center gap-2 px-3 py-2 rounded text-sm shadow-lg",
          toast.type === "ok"
            ? "bg-bull/15 border border-bull/30 text-bull"
            : "bg-bear/15 border border-bear/30 text-bear",
        )}>
          {toast.type === "ok"
            ? <CheckCircle size={14} />
            : <XCircle size={14} />}
          <span className="truncate">{toast.msg}</span>
        </div>
      )}

      <div className="flex flex-col gap-3 p-4 flex-1">
        {/* Symbol */}
        <div>
          <label className="text-[10px] uppercase tracking-wide text-muted">Symbol</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="mt-1 w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-accent"
          />
        </div>

        {/* Buy / Sell */}
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => setSide("buy")}
            className={cn(
              "py-2 rounded text-sm font-semibold border transition-colors",
              side === "buy"
                ? "bg-bull text-bg border-bull"
                : "bg-panel2 border-border text-muted hover:border-bull/50 hover:text-bull",
            )}
          >
            BUY
          </button>
          <button
            onClick={() => setSide("sell")}
            className={cn(
              "py-2 rounded text-sm font-semibold border transition-colors",
              side === "sell"
                ? "bg-bear text-bg border-bear"
                : "bg-panel2 border-border text-muted hover:border-bear/50 hover:text-bear",
            )}
          >
            SELL
          </button>
        </div>

        {/* Order type tabs */}
        <div>
          <label className="text-[10px] uppercase tracking-wide text-muted">Order Type</label>
          <div className="mt-1 grid grid-cols-4 rounded border border-border overflow-hidden">
            {ORDER_TYPES.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setOrderType(key)}
                className={cn(
                  "py-1.5 text-[11px] font-medium",
                  orderType === key
                    ? "bg-accent/20 text-accent"
                    : "bg-panel2 text-muted hover:text-text",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Quantity */}
        <div>
          <label className="text-[10px] uppercase tracking-wide text-muted">Quantity</label>
          <input
            type="number"
            min="1"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="mt-1 w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-accent"
          />
        </div>

        {/* Conditional price inputs */}
        {needsLimit && (
          <div>
            <label className="text-[10px] uppercase tracking-wide text-muted">Limit Price</label>
            <input
              type="number"
              step="0.01"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="0.00"
              className="mt-1 w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-accent"
            />
          </div>
        )}
        {needsStop && (
          <div>
            <label className="text-[10px] uppercase tracking-wide text-muted">Stop Price</label>
            <input
              type="number"
              step="0.01"
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
              placeholder="0.00"
              className="mt-1 w-full bg-panel2 border border-border rounded px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-accent"
            />
          </div>
        )}

        {/* Time in force */}
        <div>
          <label className="text-[10px] uppercase tracking-wide text-muted">Time in Force</label>
          <div className="mt-1 grid grid-cols-3 rounded border border-border overflow-hidden">
            {(["day", "gtc", "ioc"] as TIF[]).map((t) => (
              <button
                key={t}
                onClick={() => setTif(t)}
                className={cn(
                  "py-1.5 text-[11px] uppercase font-mono",
                  tif === t
                    ? "bg-accent/20 text-accent"
                    : "bg-panel2 text-muted hover:text-text",
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Preview */}
        <div className="bg-panel2 rounded border border-border px-3 py-2 text-xs font-mono text-muted">
          {preview()}
        </div>

        {/* Submit */}
        {!confirm ? (
          <button
            onClick={() => setConfirm(true)}
            disabled={!symbol || !qty}
            className={cn(
              "w-full py-2.5 rounded font-semibold text-sm transition-colors mt-auto",
              side === "buy"
                ? "bg-bull hover:bg-bull/80 text-bg disabled:opacity-40"
                : "bg-bear hover:bg-bear/80 text-bg disabled:opacity-40",
            )}
          >
            {side === "buy" ? "BUY" : "SELL"} {symbol || "—"}
          </button>
        ) : (
          <div className="flex flex-col gap-2 mt-auto">
            <div className="flex items-center gap-1.5 text-yellow-400 text-xs">
              <AlertTriangle size={12} />
              Confirm paper order
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setConfirm(false)}
                className="py-2 rounded border border-border text-sm text-muted hover:text-text"
              >
                Cancel
              </button>
              <button
                onClick={() => mutation.mutate(buildReq())}
                disabled={mutation.isPending}
                className={cn(
                  "py-2 rounded font-semibold text-sm text-bg",
                  side === "buy" ? "bg-bull" : "bg-bear",
                  mutation.isPending && "opacity-50",
                )}
              >
                {mutation.isPending ? "Placing…" : "Confirm"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
