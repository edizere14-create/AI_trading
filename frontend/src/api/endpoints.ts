import { apiClient } from "./client";
import type {
  BacktestAnalyticsResponse,
  BacktestSummaryResponse,
  HealthResponse,
  MomentumStatusResponse,
  RiskLimitsResponse,
  TradeOrder,
} from "../types/api";

export const endpoints = {
  health: () => apiClient.get<HealthResponse>("/health"),
  momentumStatus: () => apiClient.get<MomentumStatusResponse>("/momentum/status"),
  momentumStart: (symbol: string) => apiClient.post<{ status: string; symbol: string }>(`/momentum/start?symbol=${encodeURIComponent(symbol)}`),
  momentumStop: () => apiClient.post<{ status: string }>("/momentum/stop"),
  riskLimits: () => apiClient.get<RiskLimitsResponse>("/risk/limits"),
  closeAll: () => apiClient.post<{ status: string; detail: string }>("/risk/close-all"),
  backtestSummary: (days = 30, symbol = "PI_XBTUSD", timeframe = "1h") =>
    apiClient.get<BacktestSummaryResponse>(`/backtest/summary?days=${days}&symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  backtestAnalytics: (days = 30, symbol = "PI_XBTUSD", timeframe = "1h") =>
    apiClient.get<BacktestAnalyticsResponse>(`/backtest/analytics?days=${days}&symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  openOrders: async () => {
    const payload = await apiClient.get<TradeOrder[] | { orders?: TradeOrder[] }>("/trade/orders");
    if (Array.isArray(payload)) {
      return payload;
    }
    return payload.orders ?? [];
  },
};
