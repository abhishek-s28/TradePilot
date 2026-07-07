import { create } from "zustand";

export interface QuoteTick {
  bid: number;
  ask: number;
  last: number;
  mid: number;
  ts: string;
}

export interface AccountStats {
  equity: number;
  cash: number;
  buying_power: number;
  positions_value: number;
  daily_pnl: number;
  open_positions: number;
}

interface MarketStore {
  quotes: Record<string, QuoteTick>;
  account: AccountStats | null;
  connected: boolean;
  setQuotes: (q: Record<string, QuoteTick>) => void;
  setAccount: (a: AccountStats) => void;
  setConnected: (v: boolean) => void;
}

export const useMarketStore = create<MarketStore>((set) => ({
  quotes:    {},
  account:   null,
  connected: false,
  setQuotes:    (q) => set({ quotes: q }),
  setAccount:   (a) => set({ account: a }),
  setConnected: (v) => set({ connected: v }),
}));
