"use client";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card } from "@/components/primitives";
import { formatUSD } from "@/lib/utils";
import type { RiskSettings } from "@/types/api";
import { AlertTriangle, Loader2, Save, ShieldAlert, ShieldCheck } from "lucide-react";

export default function RiskPage() {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["risk-settings"],
    queryFn: () => api<RiskSettings>("/risk/settings"),
  });

  const [form, setForm] = useState<Partial<RiskSettings>>({});

  useEffect(() => {
    if (settings.data) setForm({});
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      api<RiskSettings>("/risk/settings", {
        method: "PUT",
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      setForm({});
      qc.invalidateQueries({ queryKey: ["risk-settings"] });
    },
  });

  const killSwitch = useMutation({
    mutationFn: (active: boolean) =>
      api<{ kill_switch_active: boolean }>(
        `/risk/kill-switch?active=${active}`,
        { method: "POST" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["risk-settings"] }),
  });

  const current = settings.data;
  const merged: Partial<RiskSettings> = { ...current, ...form };
  const dirty = Object.keys(form).length > 0;

  function set<K extends keyof RiskSettings>(key: K, value: RiskSettings[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  if (!current) return <div className="text-muted text-sm">Loading risk settings…</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Risk Settings</h1>

      <Card title={
        <span className="flex items-center gap-2">
          {current.kill_switch_active ? (
            <ShieldAlert size={16} className="text-bear" />
          ) : (
            <ShieldCheck size={16} className="text-bull" />
          )}
          Kill switch
        </span>
      }>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted">
            {current.kill_switch_active
              ? "Active — no new orders will be placed."
              : "Inactive — trading is allowed within limits."}
          </span>
          <button
            onClick={() => killSwitch.mutate(!current.kill_switch_active)}
            disabled={killSwitch.isPending}
            className={
              current.kill_switch_active
                ? "ml-auto px-3 py-1.5 rounded bg-bull/20 text-bull border border-bull/30 text-sm hover:bg-bull/30 disabled:opacity-50"
                : "ml-auto px-3 py-1.5 rounded bg-bear/20 text-bear border border-bear/30 text-sm hover:bg-bear/30 disabled:opacity-50"
            }
          >
            {killSwitch.isPending ? (
              <Loader2 size={14} className="animate-spin inline" />
            ) : current.kill_switch_active ? (
              "Deactivate"
            ) : (
              "Activate kill switch"
            )}
          </button>
        </div>
      </Card>

      <Card title="Limits">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <NumField
            label="Max daily loss ($)"
            value={merged.max_daily_loss_usd ?? 0}
            onChange={(v) => set("max_daily_loss_usd", v)}
            help={`Currently ${formatUSD(current.max_daily_loss_usd)}`}
          />
          <NumField
            label="Max per-trade loss ($)"
            value={merged.max_trade_loss_usd ?? 0}
            onChange={(v) => set("max_trade_loss_usd", v)}
            help={`Currently ${formatUSD(current.max_trade_loss_usd)}`}
          />
          <NumField
            label="Max open positions"
            value={merged.max_open_positions ?? 0}
            onChange={(v) => set("max_open_positions", Math.floor(v))}
            step={1}
            help={`Currently ${current.max_open_positions}`}
          />
          <NumField
            label="Max trades / day"
            value={merged.max_trades_per_day ?? 0}
            onChange={(v) => set("max_trades_per_day", Math.floor(v))}
            step={1}
            help={`Currently ${current.max_trades_per_day}`}
          />
          <NumField
            label="Max option premium ($)"
            value={merged.max_option_premium_usd ?? 0}
            onChange={(v) => set("max_option_premium_usd", v)}
            help={`Currently ${formatUSD(current.max_option_premium_usd)}`}
          />
          <NumField
            label="Cooldown after N losses"
            value={merged.cooldown_after_losses ?? 0}
            onChange={(v) => set("cooldown_after_losses", Math.floor(v))}
            step={1}
            help={`Currently ${current.cooldown_after_losses}`}
          />
        </div>

        <div className="mt-6">
          <label className="text-xs uppercase tracking-wide text-muted block mb-2">
            Allowed tickers (comma-separated, empty = all)
          </label>
          <input
            value={(merged.allowed_tickers ?? []).join(",")}
            onChange={(e) =>
              set(
                "allowed_tickers",
                e.target.value
                  .split(",")
                  .map((s) => s.trim().toUpperCase())
                  .filter(Boolean),
              )
            }
            className="w-full px-3 py-1.5 bg-panel border border-border rounded font-mono text-sm"
            placeholder="AAPL,MSFT,SPY"
          />
        </div>

        <div className="mt-6 flex items-center gap-3">
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="px-3 py-1.5 rounded bg-accent/20 text-accent border border-accent/30 text-sm flex items-center gap-2 hover:bg-accent/30 disabled:opacity-50"
          >
            {save.isPending ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            Save changes
          </button>
          {dirty && (
            <button
              onClick={() => setForm({})}
              className="px-3 py-1.5 rounded bg-panel border border-border text-sm hover:bg-panel2 text-muted"
            >
              Discard
            </button>
          )}
          {save.isError && (
            <span className="text-sm text-bear">
              Save failed: {(save.error as Error)?.message}
            </span>
          )}
        </div>
      </Card>

      <Card>
        <div className="flex items-start gap-3 text-sm text-muted">
          <AlertTriangle size={16} className="text-yellow-400 shrink-0 mt-0.5" />
          <p>
            These limits are enforced on every order — paper or live. Settings persist in
            the database. The kill switch overrides everything: with it active, no orders
            of any kind will be placed.
          </p>
        </div>
      </Card>
    </div>
  );
}

function NumField({
  label, value, onChange, step, help,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  help?: string;
}) {
  return (
    <div>
      <label className="text-xs uppercase tracking-wide text-muted block mb-1">{label}</label>
      <input
        type="number"
        value={value || ""}
        step={step ?? 1}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        className="w-full px-3 py-1.5 bg-panel border border-border rounded font-mono text-sm"
      />
      {help && <div className="mt-1 text-[10px] text-muted">{help}</div>}
    </div>
  );
}
