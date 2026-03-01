import { useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Bot,
  Briefcase,
  CandlestickChart,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LineChart,
  Settings,
  ShieldAlert,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { endpoints } from "./api/endpoints";
import { useRealtimeDashboard } from "./hooks/useRealtimeDashboard";

type ModuleKey =
  | "Dashboard"
  | "Live Trading"
  | "Backtesting"
  | "AI Models"
  | "Portfolio"
  | "Risk Management"
  | "Analytics"
  | "API Keys"
  | "Settings";

interface ModuleItem {
  key: ModuleKey;
  icon: JSX.Element;
}

const modules: ModuleItem[] = [
  { key: "Dashboard", icon: <LayoutDashboard size={16} /> },
  { key: "Live Trading", icon: <CandlestickChart size={16} /> },
  { key: "Backtesting", icon: <LineChart size={16} /> },
  { key: "AI Models", icon: <Bot size={16} /> },
  { key: "Portfolio", icon: <Briefcase size={16} /> },
  { key: "Risk Management", icon: <ShieldAlert size={16} /> },
  { key: "Analytics", icon: <BarChart3 size={16} /> },
  { key: "API Keys", icon: <KeyRound size={16} /> },
  { key: "Settings", icon: <Settings size={16} /> },
];

function Metric({ title, value, accent }: { title: string; value: string; accent?: "blue" | "green" | "red" }) {
  return (
    <div className={`metric metric-${accent ?? "blue"}`}>
      <span>{title}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function App() {
  const [active, setActive] = useState<ModuleKey>("Dashboard");
  const [symbol, setSymbol] = useState("PI_XBTUSD");
  const [timeframe, setTimeframe] = useState("1h");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [closeAllBusy, setCloseAllBusy] = useState(false);
  const { snapshot, loading, error, derived, refresh } = useRealtimeDashboard();

  const chartData = useMemo(
    () =>
      (snapshot.backtestAnalytics?.equity_curve ?? []).map((point) => ({
        ts: point.timestamp.slice(5, 16).replace("T", " "),
        equity: point.equity,
      })),
    [snapshot.backtestAnalytics]
  );

  const allocationData = [
    { name: "BTC", value: 42 },
    { name: "ETH", value: 28 },
    { name: "SOL", value: 15 },
    { name: "Cash", value: 15 },
  ];

  async function handleStartTrading() {
    await endpoints.momentumStart(symbol);
    await refresh();
  }

  async function handleStopTrading() {
    await endpoints.momentumStop();
    await refresh();
  }

  async function handleCloseAll() {
    setCloseAllBusy(true);
    try {
      await endpoints.closeAll();
      await refresh();
    } finally {
      setCloseAllBusy(false);
    }
  }

  return (
    <div className="app-root">
      <aside className={`sidebar ${sidebarOpen ? "open" : "closed"}`}>
        <div className="sidebar-header">
          <h1>AI Trading</h1>
          <button onClick={() => setSidebarOpen((v) => !v)}>{sidebarOpen ? "◀" : "▶"}</button>
        </div>
        <nav>
          {modules.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${active === item.key ? "active" : ""}`}
              onClick={() => setActive(item.key)}
            >
              {item.icon}
              {sidebarOpen && <span>{item.key}</span>}
            </button>
          ))}
        </nav>
        <button className="close-all" disabled={closeAllBusy} onClick={handleCloseAll}>
          Emergency Close All Positions
        </button>
      </aside>

      <main className="main">
        <header className="top-row">
          <div>
            <h2>{active}</h2>
            <p>Updated {new Date(snapshot.updatedAt).toLocaleTimeString()}</p>
          </div>
          <div className="realtime-pill">
            <Activity size={16} />
            <span>{loading ? "Syncing" : "Real-time"}</span>
          </div>
        </header>

        {error && <div className="error-box">{error}</div>}

        <section className="metrics-bar">
          <Metric title="Total Equity" value={`$${derived.totalEquity.toFixed(2)}`} accent="blue" />
          <Metric title="Daily PnL" value={`${derived.dailyPnl.toFixed(2)}%`} accent={derived.dailyPnl >= 0 ? "green" : "red"} />
          <Metric title="Unrealized PnL" value={`${derived.unrealizedPnl.toFixed(2)}%`} accent={derived.unrealizedPnl >= 0 ? "green" : "red"} />
          <Metric title="AI Bias" value={derived.bias} accent={derived.bias === "Bullish" ? "green" : derived.bias === "Bearish" ? "red" : "blue"} />
          <Metric title="Confidence" value={`${derived.confidence.toFixed(0)}%`} accent="blue" />
          <Metric title="Risk Exposure" value={`${derived.riskExposure.toFixed(2)}%`} accent={derived.riskExposure > 5 ? "red" : "green"} />
        </section>

        <section className="content-grid">
          <article className="panel chart-panel">
            <h3>Central Chart Area</h3>
            <div className="chart-controls">
              <label>
                Symbol
                <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
              </label>
              <label>
                Timeframe
                <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                  <option value="1m">1m</option>
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                  <option value="1d">1d</option>
                </select>
              </label>
              <button onClick={handleStartTrading}>Start</button>
              <button onClick={handleStopTrading}>Stop</button>
            </div>
            <div className="chart-canvas">
              <ResponsiveContainer width="100%" height={320}>
                <AreaChart data={chartData}>
                  <CartesianGrid stroke="#1f2937" />
                  <XAxis dataKey="ts" hide />
                  <YAxis stroke="#9ca3af" />
                  <Tooltip />
                  <Area type="monotone" dataKey="equity" stroke="#22d3ee" fill="#0ea5e933" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="chart-badges">
              <span>RSI</span>
              <span>MACD</span>
              <span>EMA</span>
              <span>VWAP</span>
              <span>Volume Profile</span>
              <span>Liquidity Heatmap</span>
              <span>AI Overlays</span>
            </div>
          </article>

          <article className="panel">
            <h3>AI Insight Panel</h3>
            <ul className="stat-list">
              <li>Current Bias: <strong>{derived.bias}</strong></li>
              <li>Confidence: <strong>{derived.confidence.toFixed(0)}%</strong></li>
              <li>Market Sentiment: <strong>{derived.dailyPnl >= 0 ? "Constructive" : "Defensive"}</strong></li>
              <li>Pattern Detection: <strong>Trend Continuation Scan</strong></li>
              <li>Volatility Forecast: <strong>{Math.abs(derived.unrealizedPnl).toFixed(2)}%</strong></li>
              <li>Risk Score: <strong>{derived.riskExposure.toFixed(2)}</strong></li>
            </ul>
            <button className="reason-btn">Why this trade?</button>
          </article>

          <article className="panel">
            <h3>Active Trades Panel</h3>
            <div className="orders-table">
              <div className="orders-head">
                <span>Side</span><span>Symbol</span><span>Qty</span><span>Price</span><span>Status</span>
              </div>
              {(snapshot.orders.length ? snapshot.orders : [{ side: "-", symbol: symbol, quantity: 0, price: 0, status: "No active order" }]).map((order, idx) => (
                <div className="orders-row" key={`${order.id ?? "row"}-${idx}`}>
                  <span>{order.side ?? "-"}</span>
                  <span>{order.symbol ?? symbol}</span>
                  <span>{order.quantity ?? 0}</span>
                  <span>{order.price ?? 0}</span>
                  <span>{order.status ?? "open"}</span>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <h3>AI Model Control Panel</h3>
            <div className="controls-grid">
              <label>Mode<select><option>Conservative</option><option>Balanced</option><option>Aggressive</option><option>Custom</option></select></label>
              <label>Risk Tolerance<input type="range" min={1} max={100} defaultValue={45} /></label>
              <label>Max Drawdown %<input type="number" defaultValue={8} /></label>
              <label>Trade Frequency Limit<input type="number" defaultValue={5} /></label>
              <label>Asset Filter<input defaultValue="BTC, ETH, SOL" /></label>
              <label>Max Leverage<input type="number" defaultValue={3} /></label>
            </div>
            <div className="mini-metrics">
              <span>Sharpe {snapshot.backtestAnalytics?.sharpe_ratio.toFixed(2) ?? "0.00"}</span>
              <span>Win Rate {snapshot.backtestAnalytics?.win_rate_pct.toFixed(2) ?? "0.00"}%</span>
              <span>Drawdown {snapshot.backtestAnalytics?.max_drawdown_pct.toFixed(2) ?? "0.00"}%</span>
            </div>
          </article>

          <article className="panel">
            <h3>Backtesting Module</h3>
            <ul className="stat-list">
              <li>Historical Simulations: <strong>Enabled</strong></li>
              <li>Slippage Modeling: <strong>{snapshot.backtestAnalytics?.slippage_bps ?? 0} bps</strong></li>
              <li>Spread Modeling: <strong>Enabled</strong></li>
              <li>Monte Carlo Simulation: <strong>Enabled</strong></li>
              <li>Parameter Optimization Heatmap: <strong>Available</strong></li>
              <li>Monthly Returns / Trade Distribution: <strong>Available</strong></li>
            </ul>
          </article>

          <article className="panel">
            <h3>Portfolio Overview</h3>
            <div className="portfolio-grid">
              <div>
                <p>Allocation Pie</p>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie dataKey="value" data={allocationData} innerRadius={42} outerRadius={72} fill="#22d3ee" />
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div>
                <p>Exposure</p>
                <ul className="stat-list">
                  <li>Long/Short Distribution: <strong>58 / 42</strong></li>
                  <li>Volatility Ranking: <strong>BTC &gt; SOL &gt; ETH</strong></li>
                  <li>Risk Heatmap: <strong>Moderate</strong></li>
                  <li>Correlation Matrix: <strong>Tracked</strong></li>
                </ul>
              </div>
            </div>
          </article>
        </section>

        {active !== "Dashboard" && (
          <section className="module-note panel">
            <h3>{active}</h3>
            <p>This module is scaffolded and wired to the same live backend data pipeline. Use sidebar to navigate the institutional workspace layout.</p>
          </section>
        )}
      </main>
    </div>
  );
}
