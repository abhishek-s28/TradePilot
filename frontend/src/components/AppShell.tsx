"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useMarketStore } from "@/lib/marketStore";
import {
  Activity, BarChart3, Bot, Briefcase,
  ClipboardList, Layers, LineChart, Menu,
  Newspaper, Settings, ShieldAlert, TrendingUp, Users, Wallet, X,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/",           label: "Dashboard",       icon: Activity      },
  { href: "/signals",    label: "Signals",          icon: TrendingUp    },
  { href: "/news",       label: "News",             icon: Newspaper     },
  { href: "/research",   label: "Research Desk",    icon: Users         },
  { href: "/portfolio",  label: "Portfolio",        icon: Briefcase     },
  { href: "/orders",     label: "Orders",           icon: ClipboardList },
  { href: "/options",    label: "Options",          icon: Layers        },
  { href: "/analytics",  label: "Analytics",        icon: LineChart     },
  { href: "/paper",      label: "Paper Trading",    icon: Wallet        },
  { href: "/risk",       label: "Risk",             icon: ShieldAlert   },
  { href: "/strategies", label: "Strategies",       icon: BarChart3     },
  { href: "/settings",   label: "Settings",         icon: Settings      },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();

  return (
    <div className="min-h-screen flex bg-bg text-text">
      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 w-56 bg-panel border-r border-border flex flex-col",
          "transform transition-transform duration-200",
          "md:translate-x-0 md:static md:flex",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        {/* Logo */}
        <div className="h-14 flex items-center justify-between px-4 border-b border-border shrink-0">
          <Link href="/" className="flex items-center gap-2 font-bold text-lg tracking-tight">
            <span className="w-6 h-6 rounded bg-accent/20 flex items-center justify-center">
              <TrendingUp size={14} className="text-accent" />
            </span>
            <span><span className="text-accent">trade</span>bot</span>
          </Link>
          <button className="md:hidden text-muted" onClick={() => setOpen(false)}>
            <X size={18} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV.map((n) => {
            const Icon = n.icon;
            const active = path === n.href || (n.href !== "/" && path?.startsWith(n.href));
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 px-4 py-2.5 text-sm transition-colors",
                  active
                    ? "bg-accent/10 text-text border-l-2 border-accent"
                    : "text-muted hover:text-text hover:bg-panel2 border-l-2 border-transparent",
                )}
              >
                <Icon size={15} />
                {n.label}
              </Link>
            );
          })}
        </nav>

        {/* Footer */}
        <div className="p-3 text-[10px] text-muted/50 border-t border-border">
          Alpaca Paper · Live data
        </div>
      </aside>

      {/* Mobile overlay */}
      {open && (
        <button
          className="fixed inset-0 z-20 bg-black/60 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="h-14 flex items-center px-4 border-b border-border bg-panel/90 backdrop-blur sticky top-0 z-10 gap-3">
          <button className="md:hidden text-muted shrink-0" onClick={() => setOpen(true)}>
            <Menu size={18} />
          </button>
          <TopBar />
        </header>

        <main className="flex-1 p-4 md:p-6 overflow-x-hidden">{children}</main>
      </div>
    </div>
  );
}

function TopBar() {
  const { account, connected } = useMarketStore();

  const market = useQuery({
    queryKey: ["market-status"],
    queryFn: () => api<{ market_open: boolean; data_provider: string; broker: string }>("/market/status"),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });

  const ms = market.data;
  const fmt = (n: number) =>
    n?.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="flex items-center gap-2 ml-auto text-xs flex-wrap">
      {/* Live equity from WS */}
      {account && (
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded bg-panel2 border border-border">
          <span className="text-muted">Equity</span>
          <span className="font-mono font-semibold text-text">${fmt(account.equity)}</span>
          <span className={cn(
            "font-mono",
            account.daily_pnl >= 0 ? "text-bull" : "text-bear"
          )}>
            {account.daily_pnl >= 0 ? "+" : ""}${fmt(account.daily_pnl)}
          </span>
        </div>
      )}

      {/* Market status */}
      {ms && (
        <span className={cn(
          "flex items-center gap-1.5 px-2 py-1 rounded border text-xs",
          ms.market_open
            ? "border-bull/30 bg-bull/10 text-bull"
            : "border-border bg-panel2 text-muted",
        )}>
          <span className={cn(
            "w-1.5 h-1.5 rounded-full",
            ms.market_open ? "bg-bull animate-pulse" : "bg-muted",
          )} />
          {ms.market_open ? "Open" : "Closed"}
        </span>
      )}

      {/* WS connection */}
      <span className={cn(
        "flex items-center gap-1 px-2 py-1 rounded border text-[10px]",
        connected
          ? "border-bull/20 text-bull/70 bg-bull/5"
          : "border-border text-muted bg-panel2",
      )}>
        <span className={cn(
          "w-1 h-1 rounded-full",
          connected ? "bg-bull animate-pulse" : "bg-muted",
        )} />
        {connected ? "Live" : "Offline"}
      </span>

      {/* Provider badge */}
      <span className="hidden md:block px-2 py-1 rounded bg-panel2 border border-border text-muted">
        {ms?.data_provider ?? "alpaca"} · {ms?.broker ?? "paper"}
      </span>
    </div>
  );
}
