import { useCallback, useEffect, useMemo, useState } from "react";
import { POLL_MS } from "../config";
import { endpoints } from "../api/endpoints";
import type { DashboardSnapshot, MarketBias } from "../types/api";

const emptySnapshot: DashboardSnapshot = {
  health: null,
  momentum: null,
  riskLimits: null,
  backtestSummary: null,
  backtestAnalytics: null,
  orders: [],
  updatedAt: new Date(0).toISOString(),
};

export function useRealtimeDashboard() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(emptySnapshot);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [health, momentum, riskLimits, backtestSummary, backtestAnalytics, orders] = await Promise.all([
        endpoints.health(),
        endpoints.momentumStatus(),
        endpoints.riskLimits().catch(() => null),
        endpoints.backtestSummary().catch(() => null),
        endpoints.backtestAnalytics().catch(() => null),
        endpoints.openOrders().catch(() => []),
      ]);

      setSnapshot({
        health,
        momentum,
        riskLimits,
        backtestSummary,
        backtestAnalytics,
        orders,
        updatedAt: new Date().toISOString(),
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown data refresh error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const derived = useMemo(() => {
    const totalEquity = snapshot.backtestSummary?.end_equity ?? snapshot.backtestAnalytics?.end_equity ?? 0;
    const dailyPnl = snapshot.backtestSummary?.total_return_pct ?? 0;
    const unrealizedPnl = snapshot.backtestAnalytics?.annualized_return_pct ?? 0;
    const riskExposure = snapshot.momentum?.risk?.drawdown_pct ? Math.abs(snapshot.momentum.risk.drawdown_pct) : 0;
    const confidence = Math.max(0, Math.min(100, Math.abs(dailyPnl) * 4 + 40));

    let bias: MarketBias = "Neutral";
    if (dailyPnl > 0.5) {
      bias = "Bullish";
    } else if (dailyPnl < -0.5) {
      bias = "Bearish";
    }

    return { totalEquity, dailyPnl, unrealizedPnl, riskExposure, confidence, bias };
  }, [snapshot]);

  return { snapshot, loading, error, derived, refresh };
}
