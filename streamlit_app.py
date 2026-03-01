"""
Advanced Institutional Dashboard for Momentum Trading.
Combines Real-time Monitoring, Portfolio Analytics, and Signal Execution.
"""
from __future__ import annotations

from typing import Any, cast
import os

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.core.config import settings
from app.services.ws_client import get_price_stream
from app.ui.pages import render_dashboard, render_portfolio, render_backtesting
from app.ui.data_client import ApiContractError, emergency_close_all_positions, get_account_balance, open_trade, add_paper_trade
from app.ui.theme import apply_theme

st.set_page_config(page_title="AI Trading Terminal", layout="wide", initial_sidebar_state="collapsed")
apply_theme()

st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Portfolio Overview", "Backtesting Dashboard"],
    index=0,
)

settings_obj = cast(Any, settings)
default_api_url = str(getattr(settings_obj, "api_base_url", "https://ai-trading-dashboard-v5dp.onrender.com"))
default_ws_url = str(getattr(settings_obj, "ws_url", "ws://127.0.0.1:8000/ws/price"))

default_mode = os.getenv("STREAMLIT_APP_MODE", "all-in-one").strip().lower()
mode_index = 0 if default_mode in {"all-in-one", "direct"} else 1
mode = st.sidebar.radio("App Mode", ["All-in-One", "Backend API"], index=mode_index)

if mode == "All-in-One":
    symbol = os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD").strip() or "BTC/USD:USD"
    api_url = "all-in-one"
    ws_url = f"kraken://{symbol}"
    st.sidebar.caption("Using direct PostgreSQL + Kraken Futures connections.")
else:
    api_url = st.sidebar.text_input("Backend URL", default_api_url)
    ws_url = st.sidebar.text_input("WebSocket URL", default_ws_url)

refresh_sec = st.sidebar.slider("Refresh (sec)", 1, 30, 2)

st.sidebar.markdown("---")
st.sidebar.subheader("Risk Preview")
preview_symbol = st.sidebar.text_input("Preview Symbol", "PI_XBTUSD")
preview_bucket_asset = st.sidebar.text_input("Bucket Asset", "BTC")
preview_bucket_limit_pct = st.sidebar.slider("Bucket Limit %", 10.0, 100.0, 60.0, 1.0)
preview_quantity = st.sidebar.number_input("Preview Quantity", min_value=0.1, value=1.0, step=0.1)

st.sidebar.markdown("---")
st.sidebar.subheader("Emergency Controls")

try:
    st.session_state.balance = float(get_account_balance(api_url))
except ApiContractError:
    st.session_state.balance = float(st.session_state.get("balance", 0.0) or 0.0)

st.sidebar.metric("Balance", f"${float(st.session_state.balance):,.2f}")
LIVE_TRADING = st.sidebar.toggle("LIVE TRADING", value=False)

if st.sidebar.button("🟢 LONG", use_container_width=True) and LIVE_TRADING:
    if float(st.session_state.balance) > 100:
        try:
            order = open_trade(api_url, "buy", amount=float(preview_quantity))
            st.sidebar.success(f"LONG submitted: {order.get('order_id', 'accepted')}")
        except ApiContractError as exc:
            st.sidebar.error(str(exc))
    else:
        st.sidebar.error("⚠️ Insufficient balance")

if st.sidebar.button("🔴 SHORT (LIVE)", use_container_width=True) and LIVE_TRADING:
    if float(st.session_state.balance) > 100:
        try:
            order = open_trade(api_url, "sell", amount=float(preview_quantity))
            st.sidebar.success(f"SHORT submitted: {order.get('order_id', 'accepted')}")
        except ApiContractError as exc:
            st.sidebar.error(str(exc))
    else:
        st.sidebar.error("⚠️ Insufficient balance")

confirm_emergency_close = st.sidebar.checkbox("Confirm emergency close-all")
if st.sidebar.button("Close All Positions", type="primary", use_container_width=True):
    if not confirm_emergency_close:
        st.sidebar.error("Enable confirmation to proceed.")
    else:
        try:
            close_result = emergency_close_all_positions(api_url)
            closed_count = int(close_result.get("closed_count", 0) or 0)
            if closed_count > 0:
                st.sidebar.success(f"Closed {closed_count} position(s).")
            else:
                st.sidebar.info(str(close_result.get("detail", "No open positions.")))
        except ApiContractError as exc:
            st.sidebar.error(str(exc))

st_autorefresh(interval=refresh_sec * 1000, key="ui_refresh")

stream = get_price_stream(ws_url)
stream.start()

st.sidebar.markdown("---")
st.sidebar.subheader("Paper Test")
if st.sidebar.button("🧪 PAPER LONG", use_container_width=True):
    paper_symbol = os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD").strip() or "BTC/USD:USD"
    paper_price = float(stream.latest.price) if float(stream.latest.price) > 0 else 0.0
    paper_size = float(os.getenv("KRAKEN_ORDER_SIZE", "1.0") or 1.0)
    try:
        trade = add_paper_trade("buy", paper_symbol, paper_size, paper_price)
        st.sidebar.success(f"Paper LONG added: {trade.get('symbol')}")
    except ApiContractError as exc:
        st.sidebar.error(str(exc))

if page == "Dashboard":
    render_dashboard(
        api_url=api_url,
        stream=stream,
        risk_preview={
            "symbol": preview_symbol,
            "bucket_asset": preview_bucket_asset,
            "bucket_limit_pct": preview_bucket_limit_pct / 100.0,
            "quantity": float(preview_quantity),
        },
    )
elif page == "Portfolio Overview":
    render_portfolio(api_url=api_url)
else:
    render_backtesting(api_url=api_url)