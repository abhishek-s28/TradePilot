"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Pill } from "@/components/primitives";
import { OrderPanel } from "@/components/OrderPanel";
import { cn } from "@/lib/utils";

interface Order {
  id: string;
  symbol: string;
  side: string;
  qty: string | number;
  order_type: string;
  limit_price?: number;
  stop_price?: number;
  status: string;
  submitted_at?: string;
  filled_at?: string;
  avg_fill_price?: number;
  filled_qty?: number;
  broker?: string;
  broker_environment?: string;
}

const STATUS_TABS = ["all", "open", "filled", "canceled"] as const;
type StatusTab = (typeof STATUS_TABS)[number];

function isOpen(status: string) {
  return ["new", "partially_filled", "accepted", "submitted", "pending"].includes(status);
}

export default function OrdersPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<StatusTab>("all");

  const orders = useQuery({
    queryKey: ["orders"],
    queryFn: () => api<Order[]>("/v1/orders"),
    refetchInterval: 3_000,
  });

  const cancel = useMutation({
    mutationFn: (id: string) => api(`/v1/orders/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });

  const all    = orders.data ?? [];
  const filtered = tab === "all"      ? all
    : tab === "open"     ? all.filter((o) => isOpen(o.status))
    : tab === "filled"   ? all.filter((o) => o.status === "filled")
    : all.filter((o) => ["canceled", "rejected"].includes(o.status));

  const fmt = (n?: number) =>
    n != null
      ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : "—";

  const statusTone = (s: string) =>
    s === "filled"     ? "bull"
    : s === "canceled" || s === "rejected" ? "default"
    : "warn";

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold tracking-tight">Orders</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Order table */}
        <div className="lg:col-span-2 space-y-3">
          {/* Tabs */}
          <div className="flex gap-1 rounded border border-border overflow-hidden w-fit">
            {STATUS_TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "px-3 py-1.5 text-xs capitalize",
                  tab === t
                    ? "bg-accent/20 text-accent font-semibold"
                    : "bg-panel2 text-muted hover:text-text",
                )}
              >
                {t}
              </button>
            ))}
          </div>

          <Card>
            {orders.isLoading && (
              <div className="text-sm text-muted animate-pulse">Loading orders…</div>
            )}
            {!orders.isLoading && filtered.length === 0 && (
              <div className="text-sm text-muted py-2">No orders in this filter.</div>
            )}
            {filtered.length > 0 && (
              <div className="overflow-x-auto -mx-1">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[10px] uppercase text-muted border-b border-border">
                      <th className="pb-2 text-left">Symbol</th>
                      <th className="pb-2 text-left">Side</th>
                      <th className="pb-2 text-right">Qty</th>
                      <th className="pb-2 text-left">Type</th>
                      <th className="pb-2 text-right">Limit</th>
                      <th className="pb-2 text-right">Fill</th>
                      <th className="pb-2 text-left">Status</th>
                      <th className="pb-2 text-left">Env</th>
                      <th className="pb-2 text-left">Time</th>
                      <th className="pb-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {filtered.map((o) => (
                      <tr key={o.id} className="group">
                        <td className="py-2.5 font-mono font-bold">{o.symbol}</td>
                        <td className="py-2.5">
                          <Pill tone={o.side === "buy" ? "bull" : "bear"}>{o.side}</Pill>
                        </td>
                        <td className="py-2.5 text-right font-mono">{o.qty}</td>
                        <td className="py-2.5 text-muted text-xs">{o.order_type}</td>
                        <td className="py-2.5 text-right font-mono text-muted">
                          {o.limit_price ? `$${fmt(o.limit_price)}` : "—"}
                        </td>
                        <td className="py-2.5 text-right font-mono">
                          {o.avg_fill_price ? `$${fmt(o.avg_fill_price)}` : "—"}
                        </td>
                        <td className="py-2.5">
                          <Pill tone={statusTone(o.status)}>{o.status}</Pill>
                        </td>
                        <td className="py-2.5">
                          <Pill tone={o.broker_environment === "live" ? "bear" : "default"}>
                            {o.broker_environment ?? "—"}
                          </Pill>
                        </td>
                        <td className="py-2.5 text-[10px] text-muted whitespace-nowrap">
                          {o.submitted_at
                            ? new Date(o.submitted_at).toLocaleString()
                            : "—"}
                        </td>
                        <td className="py-2.5 text-right">
                          {isOpen(o.status) && (
                            <button
                              onClick={() => cancel.mutate(o.id)}
                              disabled={cancel.isPending}
                              className="text-[10px] text-bear hover:underline disabled:opacity-50"
                            >
                              Cancel
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        {/* Quick order */}
        <Card title="Place Order" className="h-fit">
          <div className="-m-4 h-[500px]">
            <OrderPanel />
          </div>
        </Card>
      </div>
    </div>
  );
}
