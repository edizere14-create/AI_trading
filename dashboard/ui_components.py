import itertools
import os
import re
from urllib.parse import urlencode
from urllib.request import urlopen
import json
import pandas as pd

import streamlit as st
from engine.backtest_engine import BacktestEngine
from app.schemas import indicator


def _split_numeric_tokens(s: str):
    if s is None:
        return []
    s = str(s).strip().replace("ï¼Œ", ",")
    parts = re.split(r"[,\s;|]+", s)
    return [p for p in parts if p]


def _parse_int_list(s: str):
    return [int(float(tok)) for tok in _split_numeric_tokens(s)]


def _parse_float_list(s: str):
    return [float(tok) for tok in _split_numeric_tokens(s)]


def _fetch_json(url: str, timeout: int = 15):
    with urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def _bootstrap_significance(returns: pd.Series, periods_per_year: int = 24 * 365, n_boot: int = 500) -> dict:
    r = pd.to_numeric(returns, errors="coerce").dropna()
    if len(r) < 30:
        return {
            "samples": int(len(r)),
            "sharpe": 0.0,
            "sharpe_ci_low": 0.0,
            "sharpe_ci_high": 0.0,
            "cagr": 0.0,
            "cagr_ci_low": 0.0,
            "cagr_ci_high": 0.0,
        }

    sharpe_vals = []
    cagr_vals = []
    years = max(len(r) / float(periods_per_year), 1.0 / float(periods_per_year))
    sample_size = len(r)

    for _ in range(n_boot):
        sampled = r.sample(n=sample_size, replace=True)
        mean_r = float(sampled.mean())
        std_r = float(sampled.std())
        sharpe = (mean_r / std_r) * (periods_per_year ** 0.5) if std_r > 0 else 0.0
        sharpe_vals.append(sharpe)

        clipped = sampled.clip(lower=-0.999)
        total_return = float((1.0 + clipped).prod() - 1.0)
        cagr = ((1.0 + total_return) ** (1.0 / years) - 1.0) if (1.0 + total_return) > 0 else -1.0
        cagr_vals.append(cagr)

    sharpe_s = pd.Series(sharpe_vals)
    cagr_s = pd.Series(cagr_vals)

    base_mean = float(r.mean())
    base_std = float(r.std())
    base_sharpe = (base_mean / base_std) * (periods_per_year ** 0.5) if base_std > 0 else 0.0
    base_total = float((1.0 + r.clip(lower=-0.999)).prod() - 1.0)
    base_cagr = ((1.0 + base_total) ** (1.0 / years) - 1.0) if (1.0 + base_total) > 0 else -1.0

    return {
        "samples": int(sample_size),
        "sharpe": base_sharpe,
        "sharpe_ci_low": float(sharpe_s.quantile(0.025)),
        "sharpe_ci_high": float(sharpe_s.quantile(0.975)),
        "cagr": base_cagr,
        "cagr_ci_low": float(cagr_s.quantile(0.025)),
        "cagr_ci_high": float(cagr_s.quantile(0.975)),
    }


def _max_recovery_bars(dd_series: pd.Series) -> int:
    dd = pd.to_numeric(dd_series, errors="coerce").fillna(0.0)
    underwater = dd < 0
    run = 0
    max_run = 0
    for is_underwater in underwater:
        if is_underwater:
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    return int(max_run)


def _preset_status_badge(risk_per_trade: float, leverage: float, allow_short: bool) -> tuple[str, str]:
    if risk_per_trade <= 0.008 and leverage <= 1.5 and not allow_short:
        return "SAFE", "Prop-style conservative limits: low risk-per-trade, low leverage, no shorts."
    if risk_per_trade > 0.015 or leverage > 2.5 or allow_short:
        return "RISKY", "Outside conservative guardrails (higher risk/leverage or short exposure enabled)."
    return "MODIFIED", "Between conservative and risky thresholds."


def _compute_mtm_and_max_dd(trades: pd.DataFrame):
    if trades is None or trades.empty:
        return None, 0.0

    t = trades.copy()
    if "type" in t.columns:
        t = t[t["type"].astype(str).str.lower() != "rejected"].copy()
    if t.empty:
        return None, 0.0

    sort_cols = [c for c in ["bar_index", "timestamp"] if c in t.columns]
    if sort_cols:
        t = t.sort_values(sort_cols).reset_index(drop=True)

    # Preferred: use engine-reported balance/equity if present
    equity = None
    if "balance" in t.columns:
        b = pd.to_numeric(t["balance"], errors="coerce")
        if b.notna().any():
            equity = b.ffill().fillna(0.0)

    # Fallback: build an equity curve from cumulative PnL
    if equity is None:
        pnl_col = next((c for c in ["pnl", "realized_pnl", "profit", "net_pnl"] if c in t.columns), None)
        if pnl_col is None:
            return None, 0.0
        p = pd.to_numeric(t[pnl_col], errors="coerce").fillna(0.0)
        equity = p.cumsum()

    peak = equity.cummax()
    peak = peak.where(peak != 0, other=pd.NA)
    dd = (equity / peak) - 1.0
    dd = dd.fillna(0.0)

    # positive drawdown magnitude in [0, 1]
    max_dd = float(abs(min(0.0, float(dd.min()))))
    return equity, max_dd


def _summarize_trades(trades: pd.DataFrame) -> dict:
    if trades is None or len(trades) == 0:
        return {
            "num_trades": 0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "max_dd_pct": 0.0,
        }

    t = trades.copy()
    if "type" in t.columns:
        t = t[t["type"].astype(str).str.lower() != "rejected"].copy()

    pnl_col = next((c for c in ["pnl", "realized_pnl", "profit", "net_pnl"] if c in t.columns), None)
    if pnl_col is None:
        _, max_dd = _compute_mtm_and_max_dd(t)
        return {
            "num_trades": 0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "max_dd_pct": float(max_dd * 100.0),
        }

    t["__pnl__"] = pd.to_numeric(t[pnl_col], errors="coerce")
    closed = t.dropna(subset=["__pnl__"])

    total_pnl = float(closed["__pnl__"].sum()) if not closed.empty else 0.0
    wins = closed.loc[closed["__pnl__"] > 0, "__pnl__"]
    losses = closed.loc[closed["__pnl__"] < 0, "__pnl__"]

    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0

    profit_factor = (
        float("inf") if gross_loss == 0 and gross_profit > 0
        else (gross_profit / gross_loss if gross_loss > 0 else 0.0)
    )

    _, max_dd = _compute_mtm_and_max_dd(t)
    max_dd_pct = float(max_dd * 100.0)

    return {
        "num_trades": int(len(closed)),
        "total_pnl": total_pnl,
        "profit_factor": float(profit_factor),
        "max_dd_pct": max_dd_pct,
    }


st.set_page_config(page_title="Backtest Results", layout="wide")
st.title("Momentum Strategy Backtest")

SAFE_BASELINE = {
    "sb_lookback": 12,
    "sb_threshold": 0.008,
    "sb_qty": 0.001,
    "sb_fee_rate": 0.0006,
    "sb_slippage_pct": 0.0005,
    "sb_spread_pct": 0.0003,
    "sb_latency_steps": 1,
    "sb_stop_loss_pct": 0.012,
    "sb_take_profit_pct": 0.024,
    "sb_max_holding_bars": 24,
    "sb_risk_per_trade": 0.01,
    "sb_allow_short_selling": False,
    "sb_use_risk_sizing": True,
    "sb_max_leverage": 2.0,
    "sb_enable_margin_checks": True,
    "sb_use_walk_forward": True,
    "sb_train_size": 0.6,
    "sb_test_size": 0.2,
    "sb_step_size": 0.2,
    "sb_use_regime": True,
    "sb_atr_len": 14,
    "sb_atr_min": 0.001,
    "sb_vol_len": 20,
    "sb_vol_min": 0.0,
    "sb_spread_ui": 0.002,
    "sb_strict_prop_mode": False,
}

STRICT_PROP_LIMITS = {
    "sb_risk_per_trade": 0.008,
    "sb_max_leverage": 1.5,
    "sb_allow_short_selling": False,
}

for _k, _v in SAFE_BASELINE.items():
    st.session_state.setdefault(_k, _v)

if st.button("Reset to Safe Baseline"):
    for _k, _v in SAFE_BASELINE.items():
        st.session_state[_k] = _v
    st.rerun()

strict_prop_mode = st.checkbox(
    "Strict Prop Mode",
    key="sb_strict_prop_mode",
    help="Locks risk controls to conservative prop-style limits.",
)

if strict_prop_mode:
    st.session_state["sb_risk_per_trade"] = min(
        float(st.session_state["sb_risk_per_trade"]),
        float(STRICT_PROP_LIMITS["sb_risk_per_trade"]),
    )
    st.session_state["sb_max_leverage"] = min(
        float(st.session_state["sb_max_leverage"]),
        float(STRICT_PROP_LIMITS["sb_max_leverage"]),
    )
    st.session_state["sb_allow_short_selling"] = bool(STRICT_PROP_LIMITS["sb_allow_short_selling"])
    st.caption(
        "Strict Prop Mode active: risk/trade ≤ 0.8%, leverage ≤ 1.5x, short selling disabled."
    )

_preset_status, _preset_reason = _preset_status_badge(
    float(st.session_state["sb_risk_per_trade"]),
    float(st.session_state["sb_max_leverage"]),
    bool(st.session_state["sb_allow_short_selling"]),
)

if _preset_status == "SAFE":
    st.success(f"Current Preset Status: {_preset_status} — {_preset_reason}")
elif _preset_status == "RISKY":
    st.error(f"Current Preset Status: {_preset_status} — {_preset_reason}")
else:
    st.warning(f"Current Preset Status: {_preset_status} — {_preset_reason}")

st.caption(
    "Badge policy: SAFE if risk/trade ≤ 0.8%, leverage ≤ 1.5x, shorts off; "
    "RISKY if risk/trade > 1.5% or leverage > 2.5x or shorts on; otherwise MODIFIED."
)

st.info(
    (
        "Current Preset: "
        f"lookback={st.session_state['sb_lookback']}, "
        f"threshold={st.session_state['sb_threshold']:.4f}, "
        f"risk/trade={st.session_state['sb_risk_per_trade']:.3f}, "
        f"leverage={st.session_state['sb_max_leverage']:.1f}, "
        f"walk_forward={'on' if st.session_state['sb_use_walk_forward'] else 'off'}, "
        f"regime={'on' if st.session_state['sb_use_regime'] else 'off'}"
    )
)

run_mode = st.radio("Mode", ["Backtest (CSV)", "Live Snapshot"], horizontal=True)

if run_mode == "Live Snapshot":
    st.subheader("Live Snapshot")
    live_api_base = st.text_input("API Base URL", value=os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
    l1, l2, l3 = st.columns(3)
    live_symbol = l1.text_input("Symbol", value="PI_XBTUSD")
    live_timeframe = l2.text_input("Timeframe", value="1h")
    live_days = l3.number_input("Lookback days", min_value=7, max_value=365, value=90, step=1)

    if st.button("Fetch Live Snapshot"):
        try:
            sig_qs = urlencode(
                {
                    "symbol": live_symbol,
                    "mode": "balanced",
                    "timeframes": "1h,4h",
                    "lookback": 120,
                }
            )
            bt_qs = urlencode(
                {
                    "days": int(live_days),
                    "symbol": live_symbol,
                    "timeframe": live_timeframe,
                }
            )

            signal = _fetch_json(f"{live_api_base}/strategies/signal?{sig_qs}")
            risk_status = _fetch_json(f"{live_api_base}/risk/status")
            analytics = _fetch_json(f"{live_api_base}/backtest/analytics?{bt_qs}")

            st.markdown("#### Live Signal")
            st.json(signal)

            st.markdown("#### Live Risk Status")
            st.json(risk_status)

            st.markdown("#### Live Backtest Analytics")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Return", f"{float(analytics.get('total_return_pct', 0.0)):.2f}%")
            m2.metric("Max Drawdown", f"{float(analytics.get('max_drawdown_pct', 0.0)):.2f}%")
            m3.metric("Sharpe", f"{float(analytics.get('sharpe_ratio', 0.0)):.2f}")
            m4.metric("Trades", int(analytics.get("trades", 0)))

            monthly_perf = analytics.get("monthly_performance", [])
            if monthly_perf:
                st.dataframe(pd.DataFrame(monthly_perf), use_container_width=True)
        except Exception as exc:
            st.error(f"Failed to fetch live snapshot: {exc}")

    st.stop()

uploaded = st.file_uploader("Upload OHLCV CSV", type="csv")
if not uploaded:
    st.info("Upload CSV with: timestamp, open, high, low, close, volume")
    st.stop()

df = pd.read_csv(uploaded)
required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
if not required_cols.issubset(df.columns):
    st.error(f"Missing columns: {sorted(required_cols - set(df.columns))}")
    st.stop()

df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
for c in ["open", "high", "low", "close", "volume"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = (
    df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    .sort_values("timestamp")
    .reset_index(drop=True)
)

if len(df) < 50:
    st.warning("Dataset is small. Use 500+ rows for more realistic backtests.")
if len(df) < 10:
    st.error("Not enough rows after cleaning.")
    st.stop()

st.subheader("Strategy & Execution Parameters")
c1, c2, c3 = st.columns(3)
lookback = c1.number_input("Momentum lookback", min_value=1, max_value=200, step=1, key="sb_lookback")
threshold = c2.number_input(
    "Momentum threshold", min_value=0.0001, max_value=0.2, step=0.0001, format="%.4f", key="sb_threshold"
)
qty = c3.number_input("Fallback qty (used if risk sizing off)", min_value=0.001, max_value=1000.0, step=0.1, key="sb_qty")

c4, c5, c6, c7 = st.columns(4)
fee_rate = c4.number_input("Fee rate", min_value=0.0, max_value=0.01, step=0.0001, format="%.4f", key="sb_fee_rate")
slippage_pct = c5.number_input("Slippage %", min_value=0.0, max_value=0.01, step=0.0001, format="%.4f", key="sb_slippage_pct")
spread_pct = c6.number_input("Spread %", min_value=0.0, max_value=0.01, step=0.0001, format="%.4f", key="sb_spread_pct")
latency_steps = c7.number_input("Latency bars", min_value=0, max_value=20, step=1, key="sb_latency_steps")

st.subheader("Risk Controls")
r1, r2, r3, r4 = st.columns(4)
stop_loss_pct = r1.number_input("Stop-loss %", min_value=0.001, max_value=0.5, step=0.001, format="%.3f", key="sb_stop_loss_pct")
take_profit_pct = r2.number_input("Take-profit % (0=off)", min_value=0.0, max_value=1.0, step=0.001, format="%.3f", key="sb_take_profit_pct")
max_holding_bars = r3.number_input("Max holding bars (0=off)", min_value=0, max_value=10000, step=1, key="sb_max_holding_bars")
risk_per_trade = r4.number_input(
    "Risk per trade %",
    min_value=0.001,
    max_value=0.1,
    step=0.001,
    format="%.3f",
    key="sb_risk_per_trade",
    disabled=bool(st.session_state.get("sb_strict_prop_mode", False)),
)

s1, s2 = st.columns(2)
allow_short_selling = s1.checkbox(
    "Allow short selling",
    key="sb_allow_short_selling",
    disabled=bool(st.session_state.get("sb_strict_prop_mode", False)),
)
use_risk_sizing = s2.checkbox("Use risk-based sizing", key="sb_use_risk_sizing")

m1, m2 = st.columns(2)
max_leverage = m1.number_input(
    "Max leverage",
    min_value=1.0,
    max_value=50.0,
    step=0.5,
    key="sb_max_leverage",
    disabled=bool(st.session_state.get("sb_strict_prop_mode", False)),
)
enable_margin_checks = m2.checkbox("Enable margin checks", key="sb_enable_margin_checks")


def momentum_strategy(data: pd.DataFrame):
    sig = []
    m = data["close"].pct_change(int(lookback))
    for i in range(int(lookback), len(data)):
        if m.iloc[i] > float(threshold):
            sig.append({"index": i, "action": "buy", "quantity": float(qty)})
        elif m.iloc[i] < -float(threshold):
            sig.append({"index": i, "action": "sell", "quantity": float(qty)})
    return sig


def strat(
    data: pd.DataFrame,
    lookback: int = None,
    threshold: float = None,
    use_regime: bool = False,
    atr_len: int = 14,
    atr_min: float = 0.0,
    vol_len: int = 20,
    vol_min: float = 0.0,
    spread_max: float = float("inf"),
):
    lb = int(lookback if lookback is not None else lookback)
    th = float(threshold if threshold is not None else threshold)

    sig = []
    m = data["close"].pct_change(lb)
    for j in range(lb, len(data)):
        if m.iloc[j] > th:
            sig.append({"index": j, "action": "buy", "quantity": float(qty)})
        elif m.iloc[j] < -th:
            sig.append({"index": j, "action": "sell", "quantity": float(qty)})

    if use_regime:
        sig = indicator.filter_signals_by_regime(
            data,
            sig,
            atr_len=atr_len,
            atr_min=atr_min,
            vol_len=vol_len,
            vol_min=vol_min,
            spread_max=spread_max,
        )
    return sig


engine = BacktestEngine(
    momentum_strategy,
    fee_rate=float(fee_rate),
    slippage_pct=float(slippage_pct),
    spread_pct=float(spread_pct),
    latency_steps=int(latency_steps),
    stop_loss_pct=float(stop_loss_pct),
    take_profit_pct=(float(take_profit_pct) if take_profit_pct > 0 else None),
    max_holding_bars=(int(max_holding_bars) if max_holding_bars > 0 else None),
    allow_short_selling=allow_short_selling,
    use_risk_sizing=use_risk_sizing,
    risk_per_trade=float(risk_per_trade),
    max_leverage=float(max_leverage),
    enable_margin_checks=bool(enable_margin_checks),
)

st.subheader("Walk-Forward")
use_walk_forward = st.checkbox("Use walk-forward", key="sb_use_walk_forward")
wf1, wf2, wf3 = st.columns(3)
train_size = wf1.slider("Train size", min_value=0.1, max_value=0.9, step=0.05, key="sb_train_size")
test_size = wf2.slider("Test size", min_value=0.1, max_value=0.8, step=0.05, key="sb_test_size")
step_size = wf3.slider("Step size", min_value=0.05, max_value=0.8, step=0.05, key="sb_step_size")

if use_walk_forward:
    chunks = engine.run_walk_forward(
        df,
        train_size=float(train_size),
        test_size=float(test_size),
        step_size=float(step_size),
    )
    all_trades = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
else:
    all_trades = engine.run(df)

st.subheader("Trade Log")
if all_trades.empty:
    st.info("No trades generated.")
else:
    st.dataframe(all_trades, use_container_width=True)

if "type" in all_trades.columns:
    rejected = all_trades[all_trades["type"] == "rejected"].copy()
    rejected_count = int(len(rejected))
else:
    rejected = pd.DataFrame()
    rejected_count = 0

st.subheader("Performance Metrics")
if all_trades.empty:
    st.warning("No closed trades to analyze.")
else:
    closed = all_trades.copy()
    closed["pnl"] = pd.to_numeric(closed.get("pnl"), errors="coerce")
    closed = closed.dropna(subset=["pnl"])

    total_pnl = float(closed["pnl"].sum()) if not closed.empty else 0.0
    n_closed = int(len(closed))
    win_rate = float((closed["pnl"] > 0).mean()) if n_closed else 0.0

    wins = closed[closed["pnl"] > 0]["pnl"]
    losses = closed[closed["pnl"] < 0]["pnl"]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    expectancy = float(closed["pnl"].mean()) if n_closed else 0.0
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = (
        float("inf")
        if gross_loss == 0 and gross_profit > 0
        else (gross_profit / gross_loss if gross_loss > 0 else 0.0)
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Total PnL", f"${total_pnl:,.2f}")
    m2.metric("Closed Trades", n_closed)
    m3.metric("Win Rate", f"{win_rate * 100:.2f}%")

    m4, m5, m6 = st.columns(3)
    m4.metric("Avg Win", f"${avg_win:,.2f}")
    m5.metric("Avg Loss", f"${avg_loss:,.2f}")
    m6.metric("Expectancy", f"${expectancy:,.2f}")

    if "exit_reason" in closed.columns:
        exit_counts = (
            closed["exit_reason"]
            .fillna("unknown")
            .value_counts()
            .rename_axis("exit_reason")
            .reset_index(name="count")
        )
        exit_types_count = int(exit_counts["count"].sum()) if "count" in exit_counts.columns else 0
    else:
        exit_counts = pd.DataFrame({"exit_reason": [], "count": []})
        exit_types_count = 0

    m7, m8, m9 = st.columns(3)
    m7.metric("Profit Factor", "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}")
    m8.metric("Exit Types", exit_types_count)
    m9.metric("Rejected Orders", rejected_count)

    st.subheader("Exit Reason Breakdown")
    st.dataframe(exit_counts, use_container_width=True)

    cagr = 0.0
    max_dd = 0.0
    calmar = 0.0

    if {"type", "qty", "price", "balance", "position_side"}.issubset(all_trades.columns):
        plot_df = all_trades.copy()
        plot_df = plot_df[plot_df["type"] != "rejected"].copy()
        if not plot_df.empty:
            plot_df["qty"] = pd.to_numeric(plot_df["qty"], errors="coerce").fillna(0.0)
            plot_df["price"] = pd.to_numeric(plot_df["price"], errors="coerce").fillna(0.0)
            plot_df["balance"] = pd.to_numeric(plot_df["balance"], errors="coerce").fillna(0.0)

            sort_cols = [c for c in ["bar_index", "timestamp"] if c in plot_df.columns]
            if sort_cols:
                plot_df = plot_df.sort_values(sort_cols).reset_index(drop=True)

            def _signed_delta(row):
                t = str(row.get("type", "")).lower()
                side = str(row.get("position_side", "")).lower()
                q = float(row.get("qty", 0.0))
                if side == "long":
                    if t == "buy":
                        return q
                    if t == "sell":
                        return -q
                if side == "short":
                    if t == "sell":
                        return -q
                    if t == "buy":
                        return q
                return 0.0

            plot_df["position_after"] = plot_df.apply(_signed_delta, axis=1).cumsum()
            plot_df["equity_mtm"] = plot_df["balance"] + (plot_df["position_after"] * plot_df["price"])

            st.subheader("Equity Curve (Mark-to-Market)")
            st.line_chart(plot_df["equity_mtm"])

            st.subheader("Drawdown Curve (%)")
            peak = plot_df["equity_mtm"].cummax()
            dd_pct = ((plot_df["equity_mtm"] / peak) - 1.0) * 100.0
            max_dd_line = pd.Series([float(dd_pct.min())] * len(dd_pct), index=dd_pct.index)
            st.line_chart(pd.DataFrame({"drawdown_pct": dd_pct, "max_dd_line": max_dd_line}))

            st.subheader("Risk Ratios")
            max_dd = float(((plot_df["equity_mtm"] / peak) - 1.0).min()) if len(plot_df) else 0.0
            max_dd_abs = abs(max_dd)

            if "timestamp" in plot_df.columns:
                ts = pd.to_datetime(plot_df["timestamp"], errors="coerce")
                valid = ts.notna()
                if valid.sum() >= 2:
                    eq_ts = pd.Series(plot_df.loc[valid, "equity_mtm"].values, index=ts[valid]).sort_index()
                    start_val = float(eq_ts.iloc[0])
                    end_val = float(eq_ts.iloc[-1])

                    total_years = (eq_ts.index[-1] - eq_ts.index[0]).total_seconds() / (365.25 * 24 * 3600)
                    if total_years > 0 and start_val > 0 and end_val > 0:
                        cagr = (end_val / start_val) ** (1.0 / total_years) - 1.0
                    if max_dd_abs > 0:
                        calmar = cagr / max_dd_abs

                    returns = eq_ts.pct_change().dropna()
                    if not returns.empty:
                        downside = returns[returns < 0]
                        vol_annual = float(returns.std() * (24 * 365) ** 0.5)
                        downside_std = float(downside.std()) if not downside.empty else 0.0
                        sortino = (
                            float((returns.mean() / downside_std) * (24 * 365) ** 0.5)
                            if downside_std > 0
                            else 0.0
                        )
                        var_95 = float(returns.quantile(0.05) * 100.0)
                        cvar_sample = returns[returns <= returns.quantile(0.05)]
                        cvar_95 = float(cvar_sample.mean() * 100.0) if not cvar_sample.empty else 0.0
                        recovery_bars = _max_recovery_bars((eq_ts / eq_ts.cummax()) - 1.0)
                        median_step = eq_ts.index.to_series().diff().dropna().median()
                        recovery_time = str(recovery_bars)
                        if pd.notna(median_step):
                            recovery_time = str(recovery_bars * median_step)

                        st.subheader("Risk Metrics Table")
                        risk_table = pd.DataFrame(
                            [
                                {"metric": "Annualized Volatility", "value": f"{vol_annual * 100:.2f}%"},
                                {"metric": "Sortino Ratio", "value": f"{sortino:.2f}"},
                                {"metric": "VaR (95%)", "value": f"{var_95:.2f}%"},
                                {"metric": "CVaR (95%)", "value": f"{cvar_95:.2f}%"},
                                {"metric": "Max Drawdown", "value": f"{max_dd * 100:.2f}%"},
                                {"metric": "Calmar Ratio", "value": f"{calmar:.2f}"},
                                {"metric": "Max Recovery", "value": f"{recovery_time} ({recovery_bars} bars)"},
                            ]
                        )
                        st.dataframe(risk_table, use_container_width=True)

                        st.subheader("Statistical Significance")
                        sig = _bootstrap_significance(returns, periods_per_year=24 * 365, n_boot=500)
                        sig_table = pd.DataFrame(
                            [
                                {
                                    "metric": "Sharpe",
                                    "estimate": round(sig["sharpe"], 4),
                                    "ci_95_low": round(sig["sharpe_ci_low"], 4),
                                    "ci_95_high": round(sig["sharpe_ci_high"], 4),
                                },
                                {
                                    "metric": "CAGR",
                                    "estimate": f"{sig['cagr'] * 100:.2f}%",
                                    "ci_95_low": f"{sig['cagr_ci_low'] * 100:.2f}%",
                                    "ci_95_high": f"{sig['cagr_ci_high'] * 100:.2f}%",
                                },
                            ]
                        )
                        st.dataframe(sig_table, use_container_width=True)

                    monthly_eq = eq_ts.resample("ME").last()
                    monthly_ret = (monthly_eq.pct_change() * 100.0).dropna()
                    st.subheader("Monthly Returns (%)")
                    if monthly_ret.empty:
                        st.info("Not enough monthly history yet.")
                    else:
                        monthly_tbl = monthly_ret.to_frame(name="return_pct").reset_index()
                        monthly_tbl.columns = ["month", "return_pct"]
                        monthly_tbl["month"] = monthly_tbl["month"].dt.strftime("%Y-%m")
                        st.dataframe(monthly_tbl, use_container_width=True)

    r1, r2, r3 = st.columns(3)
    r1.metric("CAGR", f"{cagr * 100:.2f}%")
    r2.metric("Max Drawdown", f"{max_dd * 100:.2f}%")
    r3.metric("Calmar Ratio", f"{calmar:.2f}")

st.subheader("Export")
if not all_trades.empty:
    trades_csv = all_trades.to_csv(index=False).encode("utf-8")
    st.download_button("Download Trade Log CSV", trades_csv, file_name="trade_log.csv", mime="text/csv")

    summary = {
        "rows_input": [len(df)],
        "rows_trades": [len(all_trades)],
        "use_walk_forward": [use_walk_forward],
        "lookback": [int(lookback)],
        "threshold": [float(threshold)],
        "fee_rate": [float(fee_rate)],
        "slippage_pct": [float(slippage_pct)],
        "spread_pct": [float(spread_pct)],
        "latency_steps": [int(latency_steps)],
        "stop_loss_pct": [float(stop_loss_pct)],
        "take_profit_pct": [float(take_profit_pct)],
        "max_holding_bars": [int(max_holding_bars)],
    }
    summary_csv = pd.DataFrame(summary).to_csv(index=False).encode("utf-8")
    st.download_button("Download Run Summary CSV", summary_csv, file_name="run_summary.csv", mime="text/csv")

st.caption("Backtest complete.")

# Regime defaults (define before any runs/sweeps)
use_regime = False
atr_len = 14
atr_min = 0.0
vol_len = 20
vol_min = 0.0
spread_max = float("inf")

st.subheader("Parameter Sweep Grid")
run_sweep = st.checkbox("Enable parameter sweep", value=False)

if run_sweep:
    g1, g2, g3 = st.columns(3)
    lookback_grid_txt = g1.text_input("Lookback grid (csv)", value="3,5,10")
    threshold_grid_txt = g2.text_input("Threshold grid (csv)", value="0.003,0.005,0.008")
    stop_grid_txt = g3.text_input("Stop-loss grid (csv)", value="0.005,0.01,0.02")

    if st.button("Run Sweep"):
        try:
            lookbacks = _parse_int_list(lookback_grid_txt)
            thresholds = _parse_float_list(threshold_grid_txt)
            stop_losses = _parse_float_list(stop_grid_txt)
        except Exception as e:
            st.error(f"Invalid grid format: {e}")
            st.stop()

        if not lookbacks or not thresholds or not stop_losses:
            st.error("Grid cannot be empty.")
            st.stop()

        combos = list(itertools.product(lookbacks, thresholds, stop_losses))
        if not combos:
            st.warning("No parameter combinations to run.")
            st.stop()

        rows = []
        prog = st.progress(0.0)

        for i, (lb, th, sl) in enumerate(combos, start=1):
            def strat(data: pd.DataFrame, _lb=lb, _th=th):
                sig = []
                m = data["close"].pct_change(int(_lb))
                for j in range(int(_lb), len(data)):
                    if m.iloc[j] > _th:
                        sig.append({"index": j, "action": "buy", "quantity": float(qty)})
                    elif m.iloc[j] < -_th:
                        sig.append({"index": j, "action": "sell", "quantity": float(qty)})
                return sig

            eng = BacktestEngine(
                strat,
                fee_rate=float(fee_rate),
                slippage_pct=float(slippage_pct),
                spread_pct=float(spread_pct),
                latency_steps=int(latency_steps),
                stop_loss_pct=float(sl),
                take_profit_pct=(float(take_profit_pct) if take_profit_pct > 0 else None),
                max_holding_bars=(int(max_holding_bars) if max_holding_bars > 0 else None),
                allow_short_selling=allow_short_selling,
                use_risk_sizing=use_risk_sizing,
                risk_per_trade=float(risk_per_trade),
                max_leverage=float(max_leverage),
                enable_margin_checks=bool(enable_margin_checks),
            )

            if use_walk_forward:
                chunks = eng.run_walk_forward(
                    df,
                    train_size=float(train_size),
                    test_size=float(test_size),
                    step_size=float(step_size),
                )
                tr = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
            else:
                tr = eng.run(
                    df,
                    params={
                        "lookback": int(lookback),
                        "threshold": float(threshold),
                        "use_regime": bool(use_regime),
                        "atr_len": int(atr_len),
                        "atr_min": float(atr_min),
                        "vol_len": int(vol_len),
                        "vol_min": float(vol_min),
                        "spread_max": float(spread_max),
                    },
                )

            s = _summarize_trades(tr)
            rows.append(
                {
                    "lookback": lb,
                    "threshold": th,
                    "stop_loss_pct": sl,
                    **s,
                }
            )
            prog.progress(i / len(combos))

        sweep_df = pd.DataFrame(rows)
        sweep_df = sweep_df.sort_values(
            by=["total_pnl", "profit_factor", "max_dd_pct"],
            ascending=[False, False, True],
        ).reset_index(drop=True)

        st.subheader("Sweep Results")
        st.dataframe(sweep_df, use_container_width=True)

        st.download_button(
            "Download Sweep Results CSV",
            sweep_df.to_csv(index=False).encode("utf-8"),
            file_name="parameter_sweep_results.csv",
            mime="text/csv",
        )

compare_regime = st.checkbox("Compare Regime Filter", value=False)

with st.sidebar.expander("Regime Filter", expanded=False):
    use_regime = st.checkbox("Enable regime filter", key="sb_use_regime")
    atr_len = st.number_input("ATR length", min_value=2, max_value=200, step=1, key="sb_atr_len")
    atr_min = st.number_input("ATR min", min_value=0.0, step=0.0001, format="%.6f", key="sb_atr_min")
    vol_len = st.number_input("Volume length", min_value=2, max_value=200, step=1, key="sb_vol_len")
    vol_min = st.number_input("Volume min", min_value=0.0, step=1.0, key="sb_vol_min")
    _spread_ui = st.number_input("Max spread (0 = ignore)", min_value=0.0, step=0.0001, format="%.6f", key="sb_spread_ui")
    spread_max = float("inf") if _spread_ui == 0 else float(_spread_ui)

# Ensure these exist BEFORE compare block
engine_kwargs = dict(
    fee_rate=float(fee_rate),
    slippage_pct=float(slippage_pct),
    spread_pct=float(spread_pct),
    latency_steps=int(latency_steps),
    stop_loss_pct=float(stop_loss_pct) if stop_loss_pct > 0 else None,
    take_profit_pct=float(take_profit_pct) if take_profit_pct > 0 else None,
    max_holding_bars=int(max_holding_bars) if max_holding_bars > 0 else None,
    allow_short_selling=allow_short_selling,
    use_risk_sizing=use_risk_sizing,
    risk_per_trade=float(risk_per_trade),
    max_leverage=float(max_leverage),
    enable_margin_checks=bool(enable_margin_checks),
)

if compare_regime:
    eng_cmp = BacktestEngine(strat, **engine_kwargs)

    tr_no = eng_cmp.run(
        df,
        params={
            "lookback": int(lookback),
            "threshold": float(threshold),
            "use_regime": False,
        },
    )

    tr_yes = eng_cmp.run(
        df,
        params={
            "lookback": int(lookback),
            "threshold": float(threshold),
            "use_regime": True,
            "atr_len": int(atr_len),
            "atr_min": float(atr_min),
            "vol_len": int(vol_len),
            "vol_min": float(vol_min),
            "spread_max": float(spread_max),
        },
    )

    s_no = _summarize_trades(tr_no)
    s_yes = _summarize_trades(tr_yes)

    st.subheader("Regime Filter Impact")
    st.dataframe(
        pd.DataFrame([{"case": "no_regime", **s_no}, {"case": "with_regime", **s_yes}]),
        use_container_width=True,
    )
