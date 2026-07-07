// Mirrors backend/app/models/domain.py shape (subset).
export type AssetClass = "stock" | "option";
export type Side = "buy" | "sell";
export type Direction = "bullish" | "bearish" | "neutral";
export type SignalStatus =
  | "new" | "approved" | "rejected" | "expired" | "executed" | "paper_executed";
export type OrderStatus =
  | "pending" | "submitted" | "partially_filled" | "filled" | "canceled" | "rejected";

export interface Signal {
  id: string;
  strategy: string;
  asset_class: AssetClass;
  symbol: string;
  underlying?: string;
  direction: Direction;
  entry: number;
  stop_loss: number;
  take_profit: number;
  confidence: number;
  reason: string;
  invalidation?: string;
  risk_reward?: number | null;
  suggested_qty: number;
  suitable_for_options: boolean;
  status: SignalStatus;
  generated_at: string;
  metadata?: Record<string, unknown>;
}

export interface AccountSnapshot {
  cash: number;
  equity: number;
  buying_power: number;
  positions_value: number;
  daily_pnl: number;
  open_positions: number;
}

export interface Position {
  symbol: string;
  asset_class: AssetClass;
  qty: number;
  avg_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  opened_at: string;
}

export interface OptionResult {
  symbol: string;
  underlying: string;
  expiration: string;
  strike: number;
  right: "call" | "put";
  bid: number;
  ask: number;
  mid: number;
  volume: number;
  open_interest: number;
  implied_volatility: number | null;
  delta: number | null;
  spread_pct: number;
  liquidity_score: number;
  dte: number;
}

export interface RiskSettings {
  max_daily_loss_usd: number;
  max_trade_loss_usd: number;
  max_open_positions: number;
  max_trades_per_day: number;
  max_option_premium_usd: number;
  cooldown_after_losses: number;
  allowed_strategies: string[];
  allowed_tickers: string[];
  kill_switch_active: boolean;
  auto_trading_enabled: boolean;
}

export interface MarketStatus {
  market_open: boolean;
  equity_session_open: boolean;
  market_clock: {
    is_open: boolean;
    timestamp: string;
    next_open: string | null;
    next_close: string | null;
    session?: string;
    phase?: string;
    equity_tradable?: boolean;
    options_tradable?: boolean;
    extended_hours?: boolean;
  };
  data_provider: string;
  broker: string;
  broker_connected: boolean;
  trading_mode: string;
  live_trading_enabled: boolean;
  live_trading_unlocked: boolean;
  can_trade_live: boolean;
  time: string;
}

export interface UniverseExchange {
  id: string;
  label: string;
  exchange: string;
  description: string;
  count: number;
}

export interface UniverseList {
  exchange: string;
  label: string;
  description: string;
  count: number;
  symbols: string[];
}

export interface UniverseSearchResult {
  symbol: string;
  name?: string;
  exchange?: string;
  sector?: string;
  source: "curated" | "live";
}

export interface UniverseSearchResponse {
  query: string;
  count: number;
  results: UniverseSearchResult[];
}
