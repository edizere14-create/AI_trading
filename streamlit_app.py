"""
Advanced Institutional Dashboard for Momentum Trading.
Combines Real-time Monitoring, Portfolio Analytics, and Signal Execution.
"""
from __future__ import annotations

from typing import Any, cast
import base64
import binascii
import os
import requests

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.core.config import settings
from app.services.ws_client import get_price_stream
from app.ui.pages import render_dashboard, render_portfolio, render_backtesting
from app.ui.data_client import (
    ApiContractError,
    add_paper_trade,
    emergency_close_all_positions,
    get_account_balance,
    get_ai_insight,
    open_trade,
)
from app.ui.theme import apply_theme

HTTP_TIMEOUT_SEC = 3
MAX_RISK_PER_TRADE = 0.02
MAX_DAILY_LOSS_PCT = 0.05


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _api_is_healthy(api_url: str) -> bool:
    if str(api_url).strip().lower() in {"all-in-one", "direct"}:
        return True
    try:
        r = requests.get(f"{api_url.rstrip('/')}/health", timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            return False
        payload = r.json() if r.content else {}
        if isinstance(payload, dict):
            return str(payload.get("status", "")).lower() in {"healthy", "ok"}
        return True
    except Exception:
        return False


def _load_futures_symbols(api_url: str, default_symbol: str) -> list[str]:
    env_symbols = [
        item.strip().upper()
        for item in os.getenv("FUTURES_SYMBOLS_CSV", "").split(",")
        if item.strip()
    ]
    fallback = env_symbols or ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "FI_XBTUSD", "PI_XBTUSD"]

    if str(api_url).strip().lower() in {"all-in-one", "direct"}:
        return list(dict.fromkeys([default_symbol, *fallback]))

    candidate_paths = [
        "/data/contracts",
        "/data/symbols",
        "/data/markets",
    ]
    for path in candidate_paths:
        try:
            r = requests.get(f"{api_url.rstrip('/')}{path}", timeout=HTTP_TIMEOUT_SEC)
            if r.status_code != 200:
                continue
            payload = r.json()
            symbols: list[str] = []
            if isinstance(payload, dict):
                for key in ("symbols", "contracts", "markets", "data"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                symbols.append(item.strip().upper())
                            elif isinstance(item, dict):
                                symbol = item.get("symbol") or item.get("id") or item.get("name")
                                if symbol:
                                    symbols.append(str(symbol).strip().upper())
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, str):
                        symbols.append(item.strip().upper())
                    elif isinstance(item, dict):
                        symbol = item.get("symbol") or item.get("id") or item.get("name")
                        if symbol:
                            symbols.append(str(symbol).strip().upper())

            symbols = [s for s in symbols if s]
            if symbols:
                return list(dict.fromkeys([default_symbol, *symbols]))
        except Exception:
            continue

    return list(dict.fromkeys([default_symbol, *fallback]))


def _all_in_one_credential_preflight() -> tuple[bool, str]:
    api_key = os.getenv("KRAKEN_API_KEY", "").strip()
    api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()

    if not api_key or not api_secret:
        return False, "KRAKEN_API_KEY/KRAKEN_API_SECRET must be set."

    if any(ch.isspace() for ch in api_secret):
        return False, "KRAKEN_API_SECRET contains whitespace; remove spaces/newlines/quotes."

    try:
        base64.b64decode(api_secret, validate=True)
    except (binascii.Error, ValueError):
        return False, "KRAKEN_API_SECRET is not valid base64 (padding/format mismatch)."

    return True, ""


def _derive_ws_url_from_api(api_url: str) -> str:
    base = str(api_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        return f"wss://{base[len('https://'): ]}/ws/price"
    if base.startswith("http://"):
        return f"ws://{base[len('http://'): ]}/ws/price"
    return "wss://ai-trading-engd.onrender.com/ws/price"

st.set_page_config(page_title="AI Trading Terminal", layout="wide", initial_sidebar_state="collapsed")
apply_theme()

st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Portfolio Overview", "Backtesting Dashboard"],
    index=0,
)

settings_obj = cast(Any, settings)
default_api_url = os.getenv("API_BASE_URL", str(getattr(settings_obj, "api_base_url", "http://127.0.0.1:8000"))).strip()
configured_ws_url = os.getenv("WS_URL", str(getattr(settings_obj, "ws_url", ""))).strip()
if configured_ws_url and "127.0.0.1" not in configured_ws_url and "localhost" not in configured_ws_url:
    default_ws_url = configured_ws_url
else:
    default_ws_url = _derive_ws_url_from_api(default_api_url)

default_mode = os.getenv("STREAMLIT_APP_MODE", "backend-api").strip().lower()
is_production = os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
all_in_one_modes = {"all-in-one", "all_in_one", "direct", "standalone"}
backend_api_modes = {"backend-api", "backend_api", "api", "backend"}
if default_mode in all_in_one_modes:
    mode_index = 0
elif default_mode in backend_api_modes:
    mode_index = 1
else:
    mode_index = 0
mode = st.sidebar.radio("App Mode", ["All-in-One", "Backend API"], index=mode_index)

if is_production and mode == "All-in-One":
    st.sidebar.warning("All-in-One is disabled in production for safety. Using Backend API mode.")
    mode = "Backend API"


def _all_in_one_ready() -> bool:
    api_key = os.getenv("KRAKEN_API_KEY", "").strip()
    api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    return bool(api_key and api_secret and database_url)

if mode == "All-in-One":
    if not _all_in_one_ready():
        st.sidebar.warning("All-in-One requires KRAKEN_API_KEY, KRAKEN_API_SECRET, and DATABASE_URL. Falling back to Backend API mode.")
        mode = "Backend API"
        api_url = st.sidebar.text_input("Backend URL", default_api_url)
        ws_url = st.sidebar.text_input("WebSocket URL", default_ws_url)
    else:
        credentials_ok, credentials_reason = _all_in_one_credential_preflight()
        if not credentials_ok:
            st.sidebar.warning(f"All-in-One credential preflight failed ({credentials_reason}). Falling back to Backend API mode.")
            mode = "Backend API"
            api_url = st.sidebar.text_input("Backend URL", default_api_url)
            ws_url = st.sidebar.text_input("WebSocket URL", default_ws_url)
        else:
            symbol = os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD").strip() or "BTC/USD:USD"
            api_url = "all-in-one"
            ws_url = f"kraken://{symbol}"
            st.sidebar.caption("Using direct PostgreSQL + Kraken Futures connections.")
            try:
                _ = get_ai_insight(api_url)
            except Exception as exc:
                st.sidebar.warning(f"All-in-One runtime check failed ({exc}). Falling back to Backend API mode.")
                mode = "Backend API"
                api_url = st.sidebar.text_input("Backend URL", default_api_url)
                ws_url = st.sidebar.text_input("WebSocket URL", default_ws_url)
else:
    api_url = st.sidebar.text_input("Backend URL", default_api_url)
    ws_url = st.sidebar.text_input("WebSocket URL", default_ws_url)

refresh_sec = st.sidebar.slider("Refresh (sec)", 1, 30, 2)

st.sidebar.markdown("---")
st.sidebar.subheader("Webhook URLs")
if str(api_url).strip().lower() in {"all-in-one", "direct"}:
    st.sidebar.caption("Switch to Backend API mode to use HTTP webhook endpoints.")
else:
    webhook_base = api_url.rstrip("/")
    tv_url_primary = f"{webhook_base}/api/webhooks/tradingview"
    tv_url_alias = f"{webhook_base}/webhook/tradingview"
    st.sidebar.text_input("TradingView URL (Primary)", value=tv_url_primary, key="tv_webhook_primary")
    st.sidebar.text_input("TradingView URL (Alias)", value=tv_url_alias, key="tv_webhook_alias")

st.sidebar.markdown("---")
st.sidebar.subheader("Risk Preview")
default_preview_symbol = os.getenv("MOMENTUM_DEFAULT_SYMBOL", "PF_XBTUSD").strip().upper() or "PF_XBTUSD"
if default_preview_symbol.startswith("PI_"):
    default_preview_symbol = default_preview_symbol.replace("PI_", "PF_", 1)
futures_symbols = _load_futures_symbols(api_url=api_url, default_symbol=default_preview_symbol)
if default_preview_symbol not in futures_symbols:
    futures_symbols = [default_preview_symbol] + futures_symbols
preview_symbol = st.sidebar.selectbox("Futures Contract", options=futures_symbols, index=futures_symbols.index(default_preview_symbol))
preview_bucket_asset = st.sidebar.text_input("Bucket Asset", "BTC")
preview_bucket_limit_pct = st.sidebar.slider("Bucket Limit %", 10.0, 100.0, 60.0, 1.0)
preview_quantity = st.sidebar.number_input("Preview Quantity", min_value=0.1, value=1.0, step=0.1)
preview_collateral_assets = st.sidebar.text_input("Collateral Assets", "USDT,USDC,BTC")
preview_collateral_total_usd = st.sidebar.number_input("Collateral Total (USD)", min_value=100.0, value=3000.0, step=100.0)

st.sidebar.markdown("---")
st.sidebar.subheader("Emergency Controls")

api_healthy = _api_is_healthy(api_url)
st.sidebar.caption("API Health: ✅ Healthy" if api_healthy else "API Health: ❌ Unreachable")

try:
    st.session_state.balance = float(get_account_balance(api_url))
    account_synced = True
except ApiContractError:
    st.session_state.balance = float(st.session_state.get("balance", 0.0) or 0.0)
    account_synced = False

if "daily_realized_pnl" not in st.session_state:
    st.session_state.daily_realized_pnl = 0.0

st.sidebar.metric("Balance", f"${float(st.session_state.balance):,.2f}")
LIVE_TRADING = st.sidebar.toggle("LIVE TRADING", value=False)
confirm_live_trade = st.sidebar.checkbox("Confirm LIVE order")

if "stream" not in st.session_state or st.session_state.get("stream_ws_url") != ws_url:
    st.session_state.stream = get_price_stream(ws_url)
    st.session_state.stream.start()
    st.session_state.stream_ws_url = ws_url

stream = st.session_state.stream
stream_connected = _safe_float(getattr(stream.latest, "price", 0.0), 0.0) > 0
st.sidebar.caption("Stream: ✅ Connected" if stream_connected else "Stream: ❌ Waiting for ticks")


def _risk_guard(quantity: float, current_price: float, balance: float) -> str | None:
    if balance <= 0:
        return "Insufficient balance."
    if quantity <= 0:
        return "Quantity must be greater than 0."
    if current_price <= 0:
        return "No live price available; wait for stream sync."

    notional = quantity * current_price
    max_notional = balance * MAX_RISK_PER_TRADE
    if notional > max_notional:
        return (
            f"Risk exceeds limit: order notional ${notional:,.2f} > "
            f"${max_notional:,.2f} ({MAX_RISK_PER_TRADE * 100:.1f}% cap)."
        )

    daily_loss = _safe_float(st.session_state.get("daily_realized_pnl", 0.0), 0.0)
    if daily_loss <= -(balance * MAX_DAILY_LOSS_PCT):
        return (
            f"Daily loss cap reached ({MAX_DAILY_LOSS_PCT * 100:.1f}%). "
            "Trading is blocked for this session."
        )
    return None


current_price = _safe_float(getattr(stream.latest, "price", 0.0), 0.0)
balance = _safe_float(st.session_state.get("balance", 0.0), 0.0)
live_order_disabled = not (LIVE_TRADING and confirm_live_trade and api_healthy and stream_connected and account_synced)

if st.sidebar.button("🟢 LONG", use_container_width=True, disabled=live_order_disabled):
    guard_error = _risk_guard(float(preview_quantity), current_price, balance)
    if guard_error:
        st.sidebar.error(guard_error)
    else:
        try:
            order = open_trade(api_url, "buy", amount=float(preview_quantity), symbol=preview_symbol)
            st.sidebar.success(f"LONG submitted: {order.get('order_id', 'accepted')}")
        except ApiContractError as exc:
            st.sidebar.error(str(exc))

if st.sidebar.button("🔴 SHORT (LIVE)", use_container_width=True, disabled=live_order_disabled):
    guard_error = _risk_guard(float(preview_quantity), current_price, balance)
    if guard_error:
        st.sidebar.error(guard_error)
    else:
        try:
            order = open_trade(api_url, "sell", amount=float(preview_quantity), symbol=preview_symbol)
            st.sidebar.success(f"SHORT submitted: {order.get('order_id', 'accepted')}")
        except ApiContractError as exc:
            st.sidebar.error(str(exc))

confirm_emergency_close = st.sidebar.checkbox("Confirm emergency close-all")
if st.sidebar.button("Close All Positions", type="primary", use_container_width=True, disabled=not api_healthy):
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

if mode == "Backend API" or page == "Portfolio Overview":
    st_autorefresh(interval=refresh_sec * 1000, key="ui_refresh")

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
            "collateral_assets": preview_collateral_assets,
            "collateral_total_usd": float(preview_collateral_total_usd),
        },
    )
elif page == "Portfolio Overview":
    render_portfolio(api_url=api_url)
else:
    render_backtesting(api_url=api_url)