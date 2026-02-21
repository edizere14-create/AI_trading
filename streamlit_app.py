
"""Streamlit dashboard for monitoring momentum demo trading."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
import streamlit as st

# Remove plotly import - use built-in Streamlit charts instead
DEFAULT_API_URL = "http://localhost:8000"


def api_get(base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{base_url}{path}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def api_post(base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.post(f"{base_url}{path}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def safe_request(fn, *args, **kwargs) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return fn(*args, **kwargs), None
    except requests.RequestException as exc:
        return None, str(exc)


st.set_page_config(page_title="Trading Monitor", layout="wide")
st.title("Kraken Futures Demo Monitor")

with st.sidebar:
    st.header("Settings")
    api_url = st.text_input("API URL", value=DEFAULT_API_URL).rstrip("/")
    symbol = st.text_input("Symbol", value="PI_XBTUSD")
    history_limit = st.slider("History Rows", min_value=10, max_value=200, value=50, step=10)
    auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)
    if st.button("Refresh Now", use_container_width=True):
        st.rerun()

# Fetch data
health, health_err = safe_request(api_get, api_url, "/health")
status, status_err = safe_request(api_get, api_url, "/momentum/status")
history, history_err = safe_request(api_get, api_url, "/momentum/history", {"limit": history_limit})

# Worker controls
st.subheader("Worker Control")
control_col1, control_col2 = st.columns(2)
is_running = bool(status and status.get("is_running"))

with control_col1:
    if st.button("Start Worker", use_container_width=True, disabled=is_running):
        data, err = safe_request(api_post, api_url, "/momentum/start", {"symbol": symbol})
        if err:
            st.error(f"Start failed: {err}")
        else:
            st.success(f"✅ {data}")

with control_col2:
    if st.button("Stop Worker", use_container_width=True, disabled=not is_running):
        data, err = safe_request(api_post, api_url, "/momentum/stop")
        if err:
            st.error(f"Stop failed: {err}")
        else:
            st.warning(f"⏹️ {data}")

if health_err:
    st.error(f"❌ Health check failed: {health_err}")
if status_err:
    st.error(f"❌ Status check failed: {status_err}")

if not status:
    st.stop()

# Metrics
risk = status.get("risk", {})
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
kpi1.metric("Worker", "🟢 Running" if is_running else "🔴 Stopped")
kpi2.metric("Symbol", status.get("symbol", "-"))
kpi3.metric("Signals", int(status.get("signal_count", 0)))
kpi4.metric("Executions", int(status.get("execution_count", 0)))
kpi5.metric("Open Positions", int(risk.get("open_positions", 0)))

st.subheader("Risk Snapshot")
r1, r2, r3, r4 = st.columns(4)
r1.metric("Account Balance", f"${risk.get('account_balance', 0):,.2f}")
r2.metric("Drawdown %", f"{risk.get('drawdown_pct', 0):.2f}%")
r3.metric("Daily Loss", f"${risk.get('daily_loss', 0):,.2f}")
r4.metric("Total PnL", f"${risk.get('total_pnl', 0):,.2f}")

# Signals table
st.subheader("Recent Signals")
signals = (history or {}).get("signals", [])
if not signals:
    st.info("No signals yet.")
else:
    df = pd.DataFrame(signals)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.sort_values("timestamp", ascending=False)
    
    st.dataframe(df, use_container_width=True, hide_index=True)
    
    # Momentum chart
    if {"timestamp", "momentum"}.issubset(df.columns):
        chart_df = df.dropna(subset=["timestamp", "momentum"]).sort_values("timestamp")
        if not chart_df.empty:
            st.subheader("Momentum Trend")
            st.line_chart(chart_df.set_index("timestamp")["momentum"], use_container_width=True)

st.caption(f"Last refresh: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

# Auto-refresh
if auto_refresh:
    import time
    time.sleep(10)
    st.rerun()