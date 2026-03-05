"""
Advanced Institutional Dashboard for Momentum Trading.
Combines Real-time Monitoring, Portfolio Analytics, and Signal Execution.
"""
from __future__ import annotations

from typing import Any, cast
from datetime import timedelta
import base64
import binascii
import hmac
import os
import time
import requests

import pandas as pd
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
INTERNAL_API_URL = "http://127.0.0.1:8000"
INTERNAL_WS_URL = "ws://127.0.0.1:8000/ws/price"
UI_ONLY_HOSTS = {"ai-trading-ujr3.onrender.com", "ai-trading-engd.onrender.com"}
LOCAL_HOST_HINTS = {"127.0.0.1", "localhost"}
RISK_PER_TRADE_MIN = 0.01
RISK_PER_TRADE_MAX = 0.02
DEFAULT_RISK_PER_TRADE = 0.015
DEFAULT_STOP_LOSS_PCT = 0.01
MICRO_SIZE_MIN = 0.0002
MICRO_SIZE_MAX = 0.001
MAX_DAILY_LOSS_PCT = 0.05


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _calc_stop_price(entry_price: float, side: str, stop_loss_pct: float) -> float:
    if entry_price <= 0:
        return 0.0
    if str(side).strip().lower() == "sell":
        return entry_price * (1.0 + stop_loss_pct)
    return entry_price * (1.0 - stop_loss_pct)


def _compute_auto_position_size(balance: float, entry_price: float, stop_price: float, risk_pct: float) -> float:
    if balance <= 0 or entry_price <= 0 or risk_pct <= 0:
        return 0.0
    per_unit_risk = abs(entry_price - stop_price)
    if per_unit_risk <= 0:
        return 0.0

    risk_budget = balance * risk_pct
    raw_qty = risk_budget / per_unit_risk
    if raw_qty < MICRO_SIZE_MIN:
        return 0.0
    return min(raw_qty, MICRO_SIZE_MAX)


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


def _health_probe(api_url: str, interval_sec: int = 30) -> bool:
    now = time.time()
    cache_key = f"health_cache::{api_url}"
    cache = st.session_state.get(cache_key)
    if isinstance(cache, dict) and now - float(cache.get("ts", 0.0)) < float(interval_sec):
        return bool(cache.get("healthy", False))

    healthy = _api_is_healthy(api_url)
    st.session_state[cache_key] = {"healthy": healthy, "ts": now}
    return healthy


def _require_dashboard_login() -> None:
    auth_enabled = os.getenv("DASHBOARD_AUTH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not auth_enabled:
        return

    expected_user = os.getenv("DASHBOARD_AUTH_USERNAME", "admin").strip() or "admin"
    expected_password = os.getenv("DASHBOARD_AUTH_PASSWORD", "").strip()
    if not expected_password:
        st.error("Dashboard auth is enabled but not configured. Set DASHBOARD_AUTH_PASSWORD.")
        st.stop()

    if st.session_state.get("dashboard_authenticated"):
        if st.sidebar.button("Log out", use_container_width=True):
            st.session_state.dashboard_authenticated = False
            st.rerun()
        return

    st.title("Dashboard Login")
    with st.form("dashboard_login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        user_ok = hmac.compare_digest(username.strip(), expected_user)
        pass_ok = hmac.compare_digest(password, expected_password)
        if user_ok and pass_ok:
            st.session_state.dashboard_authenticated = True
            st.rerun()
        st.error("Invalid credentials")

    st.stop()


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
    return INTERNAL_WS_URL


def _is_local_url(url: str) -> bool:
    text = str(url or "").strip().lower()
    return any(host in text for host in LOCAL_HOST_HINTS)


def _is_ui_only_url(url: str) -> bool:
    text = str(url or "").strip().lower()
    return any(host in text for host in UI_ONLY_HOSTS)


def _render_confidence_chart(api_url: str) -> None:
    """
    Fetches confidence history from FastAPI and renders
    a rolling calibration chart directly in the dashboard.
    """
    if str(api_url).strip().lower() in {"all-in-one", "direct"}:
        st.caption("Confidence history chart is available in Backend API mode.")
        return

    try:
        response = requests.get(
            f"{api_url.rstrip('/')}/confidence_history",
            params={"last_n": 200},
            timeout=HTTP_TIMEOUT_SEC,
        )
        if response.status_code != 200:
            st.caption(f"Confidence history unavailable (HTTP {response.status_code})")
            return

        payload = response.json()
        stats = payload.get("stats", {})
        samples = payload.get("samples", [])

    except Exception as exc:
        st.caption(f"Confidence history fetch failed: {exc}")
        return

    if not samples:
        st.info("No confidence samples yet — waiting for signals.")
        return

    df = pd.DataFrame(samples)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Keep recent window visible if the payload is large.
    if len(df) > 500:
        cutoff = df["timestamp"].max() - timedelta(days=7)
        df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    # Stats row
    if stats:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(
            "Gate Conf Mean",
            f"{stats.get('gate_conf_mean', 0):.1f}%",
        )
        c2.metric(
            "Display Conf Mean",
            f"{stats.get('display_conf_mean', 0):.1f}%",
        )
        c3.metric(
            "Max Divergence",
            f"{stats.get('divergence_max', 0) * 100:.1f}%",
        )
        c4.metric(
            "Pass Rate",
            f"{stats.get('pass_rate_pct', 0):.1f}%",
            delta="of signals",
        )
        c5.metric(
            "Saturated",
            str(stats.get("saturated_count", 0)),
            delta="≥98% signals",
        )

    # Confidence floor reference line.
    floor = float(
        stats.get("confidence_floor")
        or (samples[0].get("confidence_floor") if samples else 55.0)
        or 55.0
    )

    # Chart 1: Gate vs display confidence.
    st.markdown("**Gate vs Display Confidence**")
    st.caption(
        f"Blue = gate confidence (controls entries) | "
        f"Orange = display confidence (shown in UI) | "
        f"Reference floor = {floor:.0f}%"
    )

    chart_df = df.set_index("timestamp")[
        ["gate_confidence", "display_confidence"]
    ].rename(columns={
        "gate_confidence": "Gate Confidence %",
        "display_confidence": "Display Confidence %",
    })
    chart_df["Floor"] = floor
    st.line_chart(chart_df, use_container_width=True)

    # Pass / block annotation.
    passed = df[df["passed_gate"] == True]
    blocked = df[df["passed_gate"] == False]
    total = max(len(df), 1)

    p1, p2, p3 = st.columns(3)
    p1.metric("Total Signals", len(df))
    p2.metric(
        "Passed Gate",
        len(passed),
        delta=f"{len(passed) / total * 100:.1f}%",
        delta_color="normal",
    )
    p3.metric(
        "Blocked",
        len(blocked),
        delta=f"{len(blocked) / total * 100:.1f}%",
        delta_color="inverse",
    )

    # Chart 2: Divergence.
    if "divergence" in df.columns:
        st.markdown("**Gate vs Display Divergence**")
        st.caption("Spikes mean UI confidence and gate confidence are out of sync")
        div_df = df.set_index("timestamp")[["divergence"]].rename(
            columns={"divergence": "Divergence %"}
        )
        st.area_chart(div_df, use_container_width=True)

    # Chart 3: Component scores.
    score_cols = [c for c in [
        "trend_score", "momentum_score",
        "pattern_score", "vol_score",
        "composite",
    ] if c in df.columns]

    if score_cols:
        st.markdown("**Gate Component Scores**")
        st.caption("All scores contribute to composite")
        scores_df = df.set_index("timestamp")[score_cols].rename(
            columns={c: c.replace("_score", "").title() for c in score_cols}
        )
        st.line_chart(scores_df, use_container_width=True)

    # Chart 4: Block reason breakdown.
    if not blocked.empty and "block_reason" in blocked.columns:
        st.markdown("**Block Reason Breakdown**")
        reason_counts = (
            blocked["block_reason"]
            .fillna("unknown")
            .value_counts()
            .reset_index()
        )
        reason_counts.columns = ["Reason", "Count"]
        st.dataframe(
            reason_counts,
            use_container_width=True,
            hide_index=True,
        )

    # Calibration suggestion.
    st.divider()
    st.markdown("**🎯 Gate Calibration Suggestion**")

    if len(df) >= 10:
        p40 = float(df["gate_confidence"].quantile(0.40))
        suggested = round(p40, 1)
        pass_rate = stats.get("pass_rate_pct", 0)

        col1, col2 = st.columns(2)
        col1.metric(
            "Current Floor",
            f"{floor:.0f}%",
            delta=f"Pass rate {pass_rate:.1f}%",
            delta_color="inverse" if pass_rate < 5 else "normal",
        )
        col2.metric(
            "Suggested Floor",
            f"{suggested:.0f}%",
            delta="40th percentile of gate conf",
        )

        if pass_rate < 2:
            st.error(
                f"⛔ Pass rate {pass_rate:.1f}% is critically low — "
                f"bot is effectively not trading. "
                f"Consider: MOMENTUM_ENTRY_CONF_GATE_PCT={suggested:.0f}"
            )
        elif pass_rate < 20:
            st.warning(
                f"⚠️ Pass rate {pass_rate:.1f}% is low. "
                f"Suggested: MOMENTUM_ENTRY_CONF_GATE_PCT={suggested:.0f}"
            )
        else:
            st.success(f"✅ Pass rate {pass_rate:.1f}% looks healthy.")

        st.code(
            f"MOMENTUM_ENTRY_CONF_GATE_PCT={suggested:.0f}",
            language="bash",
        )
    else:
        st.caption(
            f"Need at least 10 samples for calibration suggestion "
            f"(have {len(df)})."
        )

    # Recent signals table.
    with st.expander("Recent signal detail", expanded=False):
        show_cols = [c for c in [
            "timestamp", "gate_confidence", "display_confidence",
            "divergence", "composite", "passed_gate", "block_reason",
            "bias", "volatility",
        ] if c in df.columns]
        st.dataframe(
            df[show_cols].sort_values("timestamp", ascending=False).head(50),
            use_container_width=True,
            hide_index=True,
        )

st.set_page_config(page_title="AI Trading Terminal", layout="wide", initial_sidebar_state="collapsed")
apply_theme()
_require_dashboard_login()

st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Portfolio Overview", "Backtesting Dashboard"],
    index=0,
)

settings_obj = cast(Any, settings)
default_api_url = os.getenv("API_BASE_URL", str(getattr(settings_obj, "api_base_url", INTERNAL_API_URL))).strip()
if (not default_api_url) or _is_ui_only_url(default_api_url):
    default_api_url = INTERNAL_API_URL

configured_ws_url = os.getenv("WS_URL", str(getattr(settings_obj, "ws_url", ""))).strip()
if configured_ws_url and not _is_ui_only_url(configured_ws_url):
    default_ws_url = configured_ws_url
else:
    default_ws_url = _derive_ws_url_from_api(default_api_url)

default_mode = os.getenv("STREAMLIT_APP_MODE", "backend-api").strip().lower()
env_is_production = os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
is_production = env_is_production or ("onrender.com" in default_api_url.lower())
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

if _is_ui_only_url(api_url):
    api_url = INTERNAL_API_URL
    st.sidebar.warning("UI URL detected in Backend URL. Using internal API URL: http://127.0.0.1:8000")

is_remote_backend = str(api_url).strip().lower().startswith("https://") or ("onrender.com" in str(api_url).strip().lower())
if _is_ui_only_url(ws_url):
    ws_url = _derive_ws_url_from_api(api_url)
    st.sidebar.warning("UI URL detected in WebSocket URL. Using API-matched WebSocket URL.")
elif _is_local_url(ws_url) and is_remote_backend:
    ws_url = _derive_ws_url_from_api(api_url)
    st.sidebar.warning("Local WebSocket URL detected with remote backend. Using remote WebSocket URL.")

refresh_sec = st.sidebar.slider("Refresh (sec)", 1, 30, 30)
auto_refresh_enabled = st.sidebar.checkbox("Auto refresh", value=False)
if st.sidebar.button("Refresh now", use_container_width=True):
    st.rerun()

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
with st.sidebar.expander("Diagnostics", expanded=False):
    st.caption("Fetch raw backend analytics payload")
    if str(api_url).strip().lower() in {"all-in-one", "direct"}:
        st.caption("Diagnostics HTTP fetch is available in Backend API mode.")
    elif st.button("Check /momentum/analytics", key="diag_momentum_analytics"):
        analytics_url = f"{api_url.rstrip('/')}/momentum/analytics"
        try:
            response = requests.get(
                analytics_url,
                params={"symbol": "PF_XBTUSD"},
                timeout=HTTP_TIMEOUT_SEC,
            )
            st.write({"status_code": response.status_code, "url": response.url})
            try:
                payload = response.json()
                st.json(payload)
            except Exception:
                st.text(response.text[:1500])
        except Exception as exc:
            st.error(f"Diagnostics request failed: {exc}")
    elif st.button("Check /momentum/orders-sync", key="diag_momentum_orders_sync"):
        sync_url = f"{api_url.rstrip('/')}/momentum/orders-sync"
        try:
            response = requests.get(
                sync_url,
                params={"limit": 25},
                timeout=HTTP_TIMEOUT_SEC,
            )
            st.write({"status_code": response.status_code, "url": response.url})
            try:
                payload = response.json()
                st.json(payload)
            except Exception:
                st.text(response.text[:1500])
        except Exception as exc:
            st.error(f"Diagnostics request failed: {exc}")
    elif st.button("Check /confidence_diagnostic", key="diag_confidence"):
        try:
            response = requests.get(
                f"{api_url.rstrip('/')}/confidence_diagnostic",
                timeout=HTTP_TIMEOUT_SEC,
            )
            payload = response.json()

            col1, col2 = st.columns(2)
            col1.metric(
                "Gate Confidence",
                f"{payload.get('gate_confidence', 0):.1f}%"
            )
            col2.metric(
                "Confidence Floor",
                f"{payload.get('confidence_floor', 0):.1f}%"
            )

            gate_pass = payload.get("gate_would_pass", False)
            if gate_pass:
                st.success("✅ Gate would PASS — entries allowed")
            else:
                st.error("❌ Gate would BLOCK — entries blocked")
                st.caption(f"Reason: {payload.get('gate_reason', 'unknown')}")

            st.metric(
                "Divergence",
                f"{payload.get('divergence', 0):.1f}%"
            )
            st.metric(
                "Sandbox",
                "Demo ✅" if payload.get("sandbox") else "Live ⚠️"
            )

            stats = payload.get("history_stats", {})
            if stats:
                st.caption(
                    f"Samples: {stats.get('samples', 0)} | "
                    f"Pass rate: {stats.get('pass_rate_pct', 0):.1f}% | "
                    f"Max divergence: {stats.get('divergence_max', 0):.2%}"
                )

            with st.expander("Full payload"):
                st.json(payload)

        except Exception as exc:
            st.error(f"Confidence diagnostic failed: {exc}")

    elif st.button("Check /confidence_history", key="diag_confidence_history"):
        try:
            response = requests.get(
                f"{api_url.rstrip('/')}/confidence_history",
                params={"last_n": 50},
                timeout=HTTP_TIMEOUT_SEC,
            )
            payload = response.json()
            stats = payload.get("stats", {})
            samples = payload.get("samples", [])

            if stats:
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Samples", stats.get("samples", 0))
                s2.metric("Pass Rate", f"{stats.get('pass_rate_pct', 0):.1f}%")
                s3.metric("Gate Mean", f"{stats.get('gate_conf_mean', 0):.1f}%")
                s4.metric("Display Mean", f"{stats.get('display_conf_mean', 0):.1f}%")

            if samples:
                df = pd.DataFrame(samples)
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                df = df.sort_values("timestamp", ascending=False)

                st.dataframe(
                    df[[
                        "timestamp", "gate_confidence",
                        "display_confidence", "divergence",
                        "passed_gate", "block_reason",
                        "composite", "trend_score",
                    ]].head(30),
                    use_container_width=True,
                    hide_index=True,
                )

                chart_df = df.sort_values("timestamp")
                if not chart_df.empty:
                    st.line_chart(
                        chart_df.set_index("timestamp")[[
                            "gate_confidence",
                            "display_confidence",
                        ]],
                        use_container_width=True,
                    )
            else:
                st.info("No confidence samples yet — wait for signals.")

        except Exception as exc:
            st.error(f"Confidence history failed: {exc}")

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
risk_per_trade_pct = (
    st.sidebar.slider(
        "Risk Per Trade %",
        RISK_PER_TRADE_MIN * 100.0,
        RISK_PER_TRADE_MAX * 100.0,
        DEFAULT_RISK_PER_TRADE * 100.0,
        0.1,
    )
    / 100.0
)
stop_loss_pct = (
    st.sidebar.slider("Stop Loss %", 0.2, 5.0, DEFAULT_STOP_LOSS_PCT * 100.0, 0.1)
    / 100.0
)
preview_collateral_assets = st.sidebar.text_input("Collateral Assets", "USDT,USDC,BTC")
preview_collateral_total_usd = st.sidebar.number_input("Collateral Total (USD)", min_value=100.0, value=3000.0, step=100.0)

st.sidebar.markdown("---")
st.sidebar.subheader("Emergency Controls")

api_healthy = _health_probe(api_url, interval_sec=30)
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


def _risk_guard(
    quantity: float,
    current_price: float,
    balance: float,
    stop_price: float,
    risk_pct: float,
) -> str | None:
    if balance <= 0:
        return "Insufficient balance."
    if quantity <= 0:
        return "Quantity must be greater than 0."
    if current_price <= 0:
        return "No live price available; wait for stream sync."
    if stop_price <= 0:
        return "Stop price unavailable."

    per_unit_risk = abs(current_price - stop_price)
    if per_unit_risk <= 0:
        return "Invalid stop-loss distance."

    risk_amount = per_unit_risk * quantity
    max_risk_amount = balance * risk_pct
    if risk_amount > (max_risk_amount + 1e-9):
        return (
            f"Risk exceeds limit: max loss ${risk_amount:,.2f} > "
            f"${max_risk_amount:,.2f} ({risk_pct * 100:.1f}% cap)."
        )

    auto_max_qty = _compute_auto_position_size(balance, current_price, stop_price, risk_pct)
    if quantity > (auto_max_qty + 1e-9):
        return f"Quantity exceeds auto-calculated max size ({quantity:.4f} > {auto_max_qty:.4f})."

    daily_loss = _safe_float(st.session_state.get("daily_realized_pnl", 0.0), 0.0)
    if daily_loss <= -(balance * MAX_DAILY_LOSS_PCT):
        return (
            f"Daily loss cap reached ({MAX_DAILY_LOSS_PCT * 100:.1f}%). "
            "Trading is blocked for this session."
        )
    return None


current_price = _safe_float(getattr(stream.latest, "price", 0.0), 0.0)
balance = _safe_float(st.session_state.get("balance", 0.0), 0.0)
long_stop_price = _calc_stop_price(current_price, "buy", stop_loss_pct)
short_stop_price = _calc_stop_price(current_price, "sell", stop_loss_pct)
auto_quantity_long = _compute_auto_position_size(balance, current_price, long_stop_price, risk_per_trade_pct)
auto_quantity_short = _compute_auto_position_size(balance, current_price, short_stop_price, risk_per_trade_pct)
preview_quantity = auto_quantity_long if auto_quantity_long > 0 else auto_quantity_short
sizing_ready = auto_quantity_long > 0 and auto_quantity_short > 0

st.sidebar.caption(
    f"Sizing formula: qty = (balance * {risk_per_trade_pct * 100:.1f}%) / |entry-stop|"
)
st.sidebar.metric("Auto Qty (LONG)", f"{auto_quantity_long:.4f}")
st.sidebar.metric("Auto Qty (SHORT)", f"{auto_quantity_short:.4f}")
if current_price > 0:
    st.sidebar.caption(
        f"Entry={current_price:,.2f} | Long SL={long_stop_price:,.2f} | Short SL={short_stop_price:,.2f}"
    )
if not sizing_ready:
    st.sidebar.warning(
        "Auto size below micro minimum (0.0002). Increase balance, widen stop, or reduce risk constraints."
    )

live_order_disabled = not (
    LIVE_TRADING and confirm_live_trade and api_healthy and stream_connected and account_synced and sizing_ready
)

if st.sidebar.button("LONG", use_container_width=True, disabled=live_order_disabled):
    guard_error = _risk_guard(
        float(auto_quantity_long),
        current_price,
        balance,
        long_stop_price,
        risk_per_trade_pct,
    )
    if guard_error:
        st.sidebar.error(guard_error)
    else:
        try:
            order = open_trade(api_url, "buy", amount=float(auto_quantity_long), symbol=preview_symbol)
            st.sidebar.success(f"LONG submitted: {order.get('order_id', 'accepted')}")
        except ApiContractError as exc:
            st.sidebar.error(str(exc))

if st.sidebar.button("SHORT (LIVE)", use_container_width=True, disabled=live_order_disabled):
    guard_error = _risk_guard(
        float(auto_quantity_short),
        current_price,
        balance,
        short_stop_price,
        risk_per_trade_pct,
    )
    if guard_error:
        st.sidebar.error(guard_error)
    else:
        try:
            order = open_trade(api_url, "sell", amount=float(auto_quantity_short), symbol=preview_symbol)
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

if auto_refresh_enabled and page in {"Dashboard", "Portfolio Overview"}:
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

    st.divider()
    with st.expander(
        "📊 Confidence Calibration",
        expanded=False,
    ):
        _render_confidence_chart(api_url)
elif page == "Portfolio Overview":
    render_portfolio(api_url=api_url)
else:
    render_backtesting(api_url=api_url)
