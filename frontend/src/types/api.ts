export type MarketBias = "Bullish" | "Bearish" | "Neutral";

export interface HealthResponse {
  status: string;
  timestamp: string;
  models_loaded: boolean;
  active_strategies: number;
}

export interface MomentumStatusResponse {
  symbol: string;
  is_running: boolean;
  signal_count: number;
  execution_count: number;
  interval: string;
  risk?: {
    account_balance?: number;
    drawdown_pct?: number;
    open_positions?: number;
    kill_switch_active?: boolean;
  };
}

export interface RiskLimitsResponse {
  max_position_size: number;
  max_daily_loss: number;
  current_drawdown: number;
  is_trading_allowed: boolean;
}

export interface BacktestPoint {
  timestamp: string;
  equity?: number;
  drawdown_pct?: number;
}

export interface BacktestAnalyticsResponse {
  symbol: string;
  timeframe: string;
  days: number;
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate_pct: number;
  profit_factor: number;
  trades: number;
  slippage_bps: number;
  start_equity: number;
  end_equity: number;
  equity_curve: Array<{ timestamp: string; equity: number }>;
  drawdown_curve: Array<{ timestamp: string; drawdown_pct: number }>;
}

export interface BacktestSummaryResponse extends BacktestAnalyticsResponse {
  analytics?: BacktestAnalyticsResponse;
}

export interface TradeOrder {
  id?: string;
  symbol?: string;
  side?: string;
  quantity?: number;
  price?: number;
  status?: string;
}

export interface DashboardSnapshot {
  health: HealthResponse | null;
  momentum: MomentumStatusResponse | null;
  riskLimits: RiskLimitsResponse | null;
  backtestSummary: BacktestSummaryResponse | null;
  backtestAnalytics: BacktestAnalyticsResponse | null;
  orders: TradeOrder[];
  updatedAt: string;
}
