from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, TypedDict

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from app.ui.data_client import (
    ApiContractError,
    get_risk_preview,
    get_active_trades,
    get_open_orders,
    get_worker_status,
    get_ai_insight,
    get_backtest,
    get_candles,
    get_metrics,
    get_portfolio,
)


class GateRow(TypedDict):
    gate: str
    threshold: float
    actual: float
    status: str
    why: str


class StreamLatest(Protocol):
    price: float
    ts: str


class PriceStream(Protocol):
    latest: StreamLatest


def _clip_score(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _pattern_score(text: str) -> float:
    t = (text or "").lower()
    bullish = ["breakout", "higher high", "bull", "uptrend", "impulse up", "accumulation"]
    bearish = ["breakdown", "lower low", "bear", "downtrend", "impulse down", "distribution"]
    bull_hits = sum(1 for k in bullish if k in t)
    bear_hits = sum(1 for k in bearish if k in t)
    if bull_hits == bear_hits:
        return 0.0
    return _clip_score((bull_hits - bear_hits) / 3.0)


def _build_trade_reasoning(
    ai: Mapping[str, Any],
    candles: pd.DataFrame,
    confidence_threshold: float = 55.0,
    conviction_threshold: float = 0.35,
    agreement_threshold: float = 0.30,
) -> dict[str, Any]:
    close = pd.to_numeric(candles.get("close"), errors="coerce").dropna()
    if close.empty:
        return {
            "headline": "No-trade: insufficient candle data.",
            "factors": pd.DataFrame(),
            "gates": [],
            "actions": ["Load at least 60 clean candles to unlock multi-factor reasoning."],
            "composite": 0.0,
        }

    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else float(close.iloc[-1])
    trend_raw = ((sma20 / sma50) - 1.0) * 100.0 if sma50 else 0.0
    trend_score = _clip_score(trend_raw / 0.40)

    ret_10 = float((close.iloc[-1] / close.iloc[-11] - 1.0) * 100.0) if len(close) >= 11 else 0.0
    momentum_score = _clip_score(ret_10 / 0.60)

    returns = close.pct_change().dropna()
    vol_ann = float(returns.tail(96).std() * (24 * 365) ** 0.5) if len(returns) >= 20 else 0.0
    vol_score = _clip_score((0.80 - vol_ann) / 0.60)

    pattern_summary = str(ai.get("pattern_summary", "") or "")
    pattern_raw = _pattern_score(pattern_summary)
    pattern_score = pattern_raw

    ai_conf = float(ai.get("confidence", 0.0) or 0.0)
    confidence_raw = ai_conf
    confidence_score = _clip_score((ai_conf - 50.0) / 25.0)

    recent_signals = ai.get("signals", []) if isinstance(ai.get("signals"), list) else []
    signal_df = pd.DataFrame(recent_signals)
    if not signal_df.empty and "side" in signal_df.columns:
        side = signal_df["side"].astype(str).str.lower()
        buys = int((side == "buy").sum())
        sells = int((side == "sell").sum())
        total = max(1, buys + sells)
        agreement_raw = (buys - sells) / total
    else:
        agreement_raw = 0.0
    agreement_score = _clip_score(agreement_raw)

    factors: list[dict[str, float | str]] = [
        {"factor": "Trend (SMA20 vs SMA50)", "raw": trend_raw, "score": trend_score, "weight": 0.25},
        {"factor": "Momentum (10-candle return %)", "raw": ret_10, "score": momentum_score, "weight": 0.20},
        {"factor": "Volatility Regime (annualized)", "raw": vol_ann, "score": vol_score, "weight": 0.15},
        {"factor": "Pattern Quality", "raw": pattern_raw, "score": pattern_score, "weight": 0.10},
        {"factor": "AI Confidence %", "raw": confidence_raw, "score": confidence_score, "weight": 0.20},
        {"factor": "Signal Agreement", "raw": agreement_raw, "score": agreement_score, "weight": 0.10},
    ]

    for f in factors:
        f["contribution"] = float(float(f["score"]) * float(f["weight"]))

    composite = float(sum(float(f["contribution"]) for f in factors))
    if composite >= 0.25:
        stance = "LONG-BIASED"
    elif composite <= -0.25:
        stance = "SHORT-BIASED"
    else:
        stance = "NEUTRAL"

    # Volume gate (mirrors worker logic)
    volume = candles.get("volume")
    if volume is not None:
        vol_series = pd.to_numeric(volume, errors="coerce").dropna()
        vol_last = float(vol_series.iloc[-1]) if not vol_series.empty else 0.0
        vol_ma20 = float(vol_series.rolling(20).mean().iloc[-1]) if len(vol_series) >= 20 else 0.0
    else:
        vol_last, vol_ma20 = 0.0, 0.0
    vol_ratio = (vol_last / vol_ma20) if vol_ma20 > 0 else 1.0
    volume_gate = vol_ratio >= 0.80

    confidence_gate = ai_conf >= float(confidence_threshold)
    conviction_gate = abs(composite) >= float(conviction_threshold)
    trend_gate = trend_score >= 0.30
    vol_gate = vol_score >= 0.0
    pattern_gate = pattern_score >= 0.25
    agreement_gate = abs(agreement_raw) >= float(agreement_threshold)

    gates: list[GateRow] = [
        {
            "gate": f"Confidence >= {float(confidence_threshold):.1f}%",
            "threshold": round(float(confidence_threshold), 4),
            "actual": round(ai_conf, 4),
            "status": "PASS" if confidence_gate else "FAIL",
            "why": "Too low for execution" if not confidence_gate else "Sufficient confidence",
        },
        {
            "gate": f"Composite conviction >= {float(conviction_threshold):.2f}",
            "threshold": round(float(conviction_threshold), 4),
            "actual": round(abs(composite), 4),
            "status": "PASS" if conviction_gate else "FAIL",
            "why": "Insufficient conviction" if not conviction_gate else "Conviction threshold met",
        },
        {
            "gate": "Trend/SMA >= 0.30",
            "threshold": 0.30,
            "actual": round(trend_score, 4),
            "status": "PASS" if trend_gate else "FAIL",
            "why": "Trend alignment too weak" if not trend_gate else "Trend alignment acceptable",
        },
        {
            "gate": "Volatility Regime >= 0.00",
            "threshold": 0.00,
            "actual": round(vol_score, 4),
            "status": "PASS" if vol_gate else "FAIL",
            "why": "Volatility regime unfavorable" if not vol_gate else "Volatility regime acceptable",
        },
        {
            "gate": "Pattern Quality >= 0.25",
            "threshold": 0.25,
            "actual": round(pattern_score, 4),
            "status": "PASS" if pattern_gate else "FAIL",
            "why": "No clear candle pattern" if not pattern_gate else "Pattern quality acceptable",
        },
        {
            "gate": f"Signal agreement >= {float(agreement_threshold):.2f}",
            "threshold": round(float(agreement_threshold), 4),
            "actual": round(abs(agreement_raw), 4),
            "status": "PASS" if agreement_gate else "FAIL",
            "why": "No confirming signals" if not agreement_gate else "Directional agreement confirmed",
        },
        {
            "gate": "Volume ratio >= 0.80",
            "threshold": 0.80,
            "actual": round(vol_ratio, 4),
            "status": "PASS" if volume_gate else "FAIL",
            "why": "Volume too low for safe entry" if not volume_gate else "Volume sufficient",
        },
    ]

    go_nogo = "GO" if (
        confidence_gate
        and conviction_gate
        and trend_gate
        and vol_gate
        and pattern_gate
        and agreement_gate
        and volume_gate
        and stance != "NEUTRAL"
    ) else "NO-GO"
    go_nogo_reason = (
        "All execution gates passed with directional conviction."
        if go_nogo == "GO"
        else "One or more execution gates failed or directional conviction is neutral."
    )

    actions: list[str] = []
    if not confidence_gate:
        actions.append(f"Wait for confidence to rise above {float(confidence_threshold):.1f}% before risking capital.")
    if not conviction_gate:
        actions.append(
            f"Require stronger factor alignment (composite score magnitude >= {float(conviction_threshold):.2f})."
        )
    if not trend_gate:
        actions.append("Require stronger trend alignment (SMA-based trend score >= 0.30).")
    if not vol_gate:
        actions.append("Wait for volatility regime to normalize before execution.")
    if not pattern_gate:
        actions.append("Wait for a clear candle/pattern setup (pattern quality >= 0.25).")
    if not agreement_gate:
        actions.append(
            f"Need directional agreement in recent signals (buy/sell imbalance >= {float(agreement_threshold):.2f})."
        )
    if not volume_gate:
        actions.append(f"Wait for volume to pick up (current ratio {vol_ratio:.2f} < 0.80 vs 20-bar MA).")
    if not actions:
        actions.append("All gates passed: eligible for execution with risk controls.")

    headline = (
        f"{stance} | Composite={composite:.3f} | "
        f"AI Confidence={ai_conf:.2f}%"
    )

    return {
        "headline": headline,
        "factors": pd.DataFrame(factors),
        "gates": gates,
        "actions": actions,
        "composite": composite,
        "go_nogo": go_nogo,
        "go_nogo_reason": go_nogo_reason,
        "volume_ratio": vol_ratio,
    }


def render_execution_debug(signal: Mapping[str, float]) -> None:
    """Show exactly why execution passed/failed."""

    confidence_value = float(signal.get("confidence_pct", 0.0) or 0.0)
    composite_value = float(signal.get("composite_score", 0.0) or 0.0)
    trend_value = float(signal.get("trend_score", 0.0) or 0.0)
    vol_value = float(signal.get("vol_score", 0.0) or 0.0)
    pattern_value = float(signal.get("pattern_quality", 0.0) or 0.0)
    agreement_value = float(signal.get("signal_agreement", 0.0) or 0.0)
    volume_ratio = float(signal.get("volume_ratio", 1.0) or 1.0)

    gate_rows: list[dict[str, float | str | bool]] = [
        {"key": "confidence", "value": confidence_value, "pass": confidence_value >= 55, "threshold": 55.0},
        {"key": "composite", "value": composite_value, "pass": composite_value >= 0.35, "threshold": 0.35},
        {"key": "trend_sma", "value": trend_value, "pass": trend_value >= 0.30, "threshold": 0.30},
        {"key": "vol_regime", "value": vol_value, "pass": True, "threshold": "Normalized"},
        {"key": "pattern", "value": pattern_value, "pass": pattern_value >= 0.25, "threshold": 0.25},
        {"key": "agreement", "value": agreement_value, "pass": agreement_value >= 0.30, "threshold": 0.30},
        {"key": "volume", "value": volume_ratio, "pass": volume_ratio >= 0.80, "threshold": 0.80},
    ]

    executed = all(bool(row["pass"]) for row in gate_rows)

    if executed:
        st.success("Execution ready.")
    else:
        failed_count = sum(1 for row in gate_rows if not bool(row["pass"]))
        st.error(f"Execution blocked — {failed_count} risk gate(s) failed.")

    if not executed:
        st.markdown("### 📋 **Actionable Next Steps**")
        next_steps = []

        if confidence_value < 55:
            next_steps.append("• Wait for confidence to rise >55%")
        if pattern_value == 0:
            next_steps.append("• Require stronger candle pattern")
        if agreement_value == 0:
            next_steps.append("• Need directional signal agreement ≥0.30")
        if volume_ratio < 0.80:
            next_steps.append(f"• Volume ratio too low ({volume_ratio:.2f} < 0.80)")

        for step in next_steps:
            st.caption(step)


def _metric_bar(metrics: Mapping[str, Any]) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Equity", f"${metrics['total_equity']:,.2f}")
    c2.metric("Daily PnL", f"${metrics['daily_pnl']:,.2f}")
    c3.metric("AI Bias", str(metrics["ai_bias"]))
    c4.metric("Confidence %", f"{float(metrics['confidence']):.2f}%")
    c5.metric("Risk Exposure %", f"{float(metrics['risk_exposure']):.2f}%")


def _chart(df: pd.DataFrame, ai: Mapping[str, Any], live_price: float) -> None:
    # Filter outlier candles (bad ticks that distort Y-axis)
    median_close = df["close"].median()
    if median_close > 0:
        lower_bound = median_close * 0.5
        upper_bound = median_close * 2.0
        mask = (
            (df["low"] >= lower_bound) & (df["high"] <= upper_bound)
            & (df["open"] >= lower_bound) & (df["close"] <= upper_bound)
        )
        if mask.sum() >= 10:
            df = df[mask].copy()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Candles"
        ),
        row=1, col=1
    )
    sma20 = df["close"].rolling(20).mean()
    fig.add_trace(go.Scatter(x=df["timestamp"], y=sma20, mode="lines", name="SMA 20"), row=1, col=1)

    if isinstance(ai.get("signals"), list):
        s = pd.DataFrame(ai["signals"])
        if not s.empty and {"timestamp", "price", "side"}.issubset(s.columns):
            s["timestamp"] = pd.to_datetime(s["timestamp"], errors="coerce", utc=True)
            s["price"] = pd.to_numeric(s["price"], errors="coerce")
            buys = s[s["side"].str.lower() == "buy"]
            sells = s[s["side"].str.lower() == "sell"]
            if not buys.empty:
                fig.add_trace(go.Scatter(x=buys["timestamp"], y=buys["price"], mode="markers", name="AI Buy"), row=1, col=1)
            if not sells.empty:
                fig.add_trace(go.Scatter(x=sells["timestamp"], y=sells["price"], mode="markers", name="AI Sell"), row=1, col=1)

    fig.add_trace(go.Bar(x=df["timestamp"], y=df["volume"], name="Volume"), row=2, col=1)

    # SL/TP drag simulation
    base = live_price if live_price > 0 else float(df["close"].iloc[-1])
    sl = st.session_state.get("sim_sl", base * 0.985)
    tp = st.session_state.get("sim_tp", base * 1.015)
    fig.add_hline(y=sl, line_dash="dash", line_color="#FF4D4F", row=1, col=1, annotation_text="SL")
    fig.add_hline(y=tp, line_dash="dash", line_color="#00D084", row=1, col=1, annotation_text="TP")
    if live_price > 0:
        fig.add_hline(y=live_price, line_dash="dot", line_color="#00D4FF", row=1, col=1, annotation_text="LIVE")

    fig.update_layout(height=720, template="plotly_dark", margin=dict(l=8, r=8, t=24, b=8))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.session_state["sim_sl"] = st.slider("SL (drag simulation)", min_value=base * 0.90, max_value=base * 1.10, value=sl)
    with c2:
        st.session_state["sim_tp"] = st.slider("TP (drag simulation)", min_value=base * 0.90, max_value=base * 1.10, value=tp)


def render_dashboard(api_url: str, stream: PriceStream, risk_preview: Mapping[str, Any] | None = None) -> None:
    try:
        metrics = get_metrics(api_url)
        ai = get_ai_insight(api_url)
        trades = get_active_trades(api_url)
        candles = get_candles(api_url, limit=300)
    except ApiContractError as e:
        st.error(f"Dashboard data load failed: {e}")
        st.info("Switch to Backend API mode, or verify All-in-One credentials/connectivity.")
        return
    except Exception as exc:
        st.error(f"Dashboard runtime error: {exc}")
        st.info("Switch to Backend API mode, or verify All-in-One exchange/data configuration.")
        return

    try:
        open_orders = get_open_orders(api_url)
    except ApiContractError:
        open_orders = pd.DataFrame()
    except Exception:
        open_orders = pd.DataFrame()
    try:
        worker_status = get_worker_status(api_url)
    except ApiContractError:
        worker_status = {}
    except Exception:
        worker_status = {}

    entry_gate_snapshot = (
        worker_status.get("last_entry_gate_snapshot", {})
        if isinstance(worker_status.get("last_entry_gate_snapshot", {}), dict)
        else {}
    )
    entry_gate_conf_raw = pd.to_numeric(entry_gate_snapshot.get("confidence_pct"), errors="coerce")
    entry_gate_confidence = float(entry_gate_conf_raw) if pd.notna(entry_gate_conf_raw) else None
    snapshot_ts_raw = entry_gate_snapshot.get("timestamp") or entry_gate_snapshot.get("evaluated_at")
    snapshot_ts = pd.to_datetime(snapshot_ts_raw, errors="coerce", utc=True)
    snapshot_age_sec: float | None = None
    if pd.notna(snapshot_ts):
        snapshot_age_sec = max(0.0, (pd.Timestamp.now(tz="UTC") - snapshot_ts).total_seconds())
    try:
        stale_after_sec = float(os.getenv("UI_ENTRY_GATE_STALE_SEC", "180") or "180")
    except (TypeError, ValueError):
        stale_after_sec = 180.0
    stale_after_sec = max(30.0, stale_after_sec)

    ai_display: dict[str, Any] = dict(ai)
    metrics_display: dict[str, Any] = dict(metrics)
    if entry_gate_confidence is not None:
        ai_display["confidence"] = entry_gate_confidence
        metrics_display["confidence"] = entry_gate_confidence

    _metric_bar(metrics_display)

    left, mid, right = st.columns([3.8, 1.6, 1.8])

    with left:
        st.subheader("Main Chart")
        st.caption(f"Live Price: ${stream.latest.price:,.2f} • {stream.latest.ts}")
        _chart(candles, ai_display, stream.latest.price)

    with mid:
        st.subheader("AI Insight Panel")
        risk_preview = risk_preview or {}
        configured_symbol = str(risk_preview.get("symbol", "PF_XBTUSD") or "PF_XBTUSD").strip().upper()
        if configured_symbol.startswith("PI_"):
            configured_symbol = configured_symbol.replace("PI_", "PF_", 1)
        preview_symbol = configured_symbol if configured_symbol else "PF_XBTUSD"
        configured_asset = str(risk_preview.get("bucket_asset", "BTC") or "BTC").strip().upper()
        preview_bucket_asset = configured_asset if configured_asset else "BTC"
        preview_bucket_limit = float(risk_preview.get("bucket_limit_pct", 0.60) or 0.60)
        preview_quantity = float(risk_preview.get("quantity", 1.0) or 1.0)
        collateral_assets_raw = str(risk_preview.get("collateral_assets", "USDT") or "USDT")
        collateral_assets = [asset.strip().upper() for asset in collateral_assets_raw.split(",") if asset.strip()]
        if not collateral_assets:
            collateral_assets = ["USDT"]
        collateral_total_usd = float(risk_preview.get("collateral_total_usd", 3000.0) or 3000.0)
        confidence_value = ai_display.get("confidence")
        confidence_text = "N/A" if confidence_value in (None, "") else f"{float(str(confidence_value)):.2f}%"
        vol_forecast_value = ai_display.get("vol_forecast")
        vol_forecast_text = "N/A" if vol_forecast_value in (None, "") else f"{float(str(vol_forecast_value)):.2f}"
        pattern_summary = str(ai_display.get("pattern_summary") or "").strip()
        pattern_text = pattern_summary if pattern_summary else "N/A"
        st.write(f"**Bias:** {ai_display.get('bias', 'N/A')}")
        st.write(f"**Confidence:** {confidence_text}")
        if entry_gate_confidence is not None:
            st.caption(f"Execution gate confidence source: worker snapshot ({entry_gate_confidence:.2f}%).")
        if snapshot_age_sec is not None:
            st.caption(f"Entry gate snapshot age: {snapshot_age_sec:.0f}s")
            if snapshot_age_sec > stale_after_sec:
                st.warning(
                    f"Entry gate snapshot appears stale ({snapshot_age_sec:.0f}s old). "
                    "Worker may be paused/disconnected."
                )
        st.write(f"**Volatility Forecast:** {vol_forecast_text}")
        st.write(f"**Pattern Detection:** {pattern_text}")
        with st.expander("Why this trade?"):
            g1, g2, g3 = st.columns(3)
            confidence_threshold = g1.slider("Conf Gate %", min_value=0.0, max_value=100.0, value=55.0, step=1.0)
            conviction_threshold = g2.slider("Conviction Gate", min_value=0.05, max_value=0.60, value=0.35, step=0.01)
            agreement_threshold = g3.slider("Agreement Gate", min_value=0.05, max_value=1.00, value=0.30, step=0.05)

            reasoning = _build_trade_reasoning(
                ai_display,
                candles,
                confidence_threshold=confidence_threshold,
                conviction_threshold=conviction_threshold,
                agreement_threshold=agreement_threshold,
            )
            if reasoning.get("go_nogo") == "GO":
                st.success(f"Execution Decision: {reasoning['go_nogo']} — {reasoning.get('go_nogo_reason', '')}")
            else:
                st.error(f"Execution Decision: {reasoning['go_nogo']} — {reasoning.get('go_nogo_reason', '')}")
            st.markdown(f"**Decision Logic:** {reasoning['headline']}")
            action_items = reasoning.get("actions", [])
            if isinstance(action_items, list) and action_items:
                st.markdown("**No-Trade Actions:**")
                for item in action_items:
                    st.caption(f"• {item}")

            provided_why = str(ai_display.get("why", "") or "").strip()
            if provided_why:
                st.markdown(f"**Model Narrative:** {provided_why}")

            gates = reasoning.get("gates", [])
            if isinstance(gates, list) and gates:
                gates_df = pd.DataFrame(gates)
                if not gates_df.empty:
                    fail_count = int((gates_df["status"].astype(str).str.upper() == "FAIL").sum())
                    if fail_count > 0:
                        st.warning(f"Gate Checklist: {fail_count} gate(s) failed")
                    else:
                        st.success("Gate Checklist: all gates passed")

                    display_gates = gates_df.copy()
                    for column in ["threshold", "actual"]:
                        if column in display_gates.columns:
                            display_gates[column] = pd.to_numeric(display_gates[column], errors="coerce").fillna(0.0).round(4)
                    st.dataframe(display_gates, use_container_width=True, hide_index=True)

            factors_df = reasoning.get("factors", pd.DataFrame())
            if not factors_df.empty:
                display_df = factors_df.copy()
                display_df["raw"] = display_df["raw"].astype(float).round(4)
                display_df["score"] = display_df["score"].astype(float).round(4)
                display_df["weight"] = display_df["weight"].astype(float).round(2)
                display_df["contribution"] = display_df["contribution"].astype(float).round(4)

                def _color_contrib(v: float | int | str) -> str:
                    x = float(v)
                    if x > 0:
                        return "color: #00D084; font-weight: 600"
                    if x < 0:
                        return "color: #FF4D4F; font-weight: 600"
                    return "color: #B0B0B0"

                styled = display_df.style.map(_color_contrib, subset=["contribution"])
                st.dataframe(styled, use_container_width=True, hide_index=True)

            signal_debug = {
                "confidence_pct": float(ai_display.get("confidence", 0.0) or 0.0),
                "composite_score": float(abs(reasoning.get("composite", 0.0))),
                "trend_score": float(
                    pd.to_numeric(factors_df.loc[factors_df["factor"] == "Trend (SMA20 vs SMA50)", "score"], errors="coerce")
                    .fillna(0.0)
                    .iloc[0]
                    if not factors_df.empty and "factor" in factors_df.columns and "score" in factors_df.columns and (factors_df["factor"] == "Trend (SMA20 vs SMA50)").any()
                    else 0.0
                ),
                "vol_score": float(
                    pd.to_numeric(factors_df.loc[factors_df["factor"] == "Volatility Regime (annualized)", "score"], errors="coerce")
                    .fillna(0.0)
                    .iloc[0]
                    if not factors_df.empty and "factor" in factors_df.columns and "score" in factors_df.columns and (factors_df["factor"] == "Volatility Regime (annualized)").any()
                    else 0.0
                ),
                "pattern_quality": float(
                    pd.to_numeric(factors_df.loc[factors_df["factor"] == "Pattern Quality", "score"], errors="coerce")
                    .fillna(0.0)
                    .iloc[0]
                    if not factors_df.empty and "factor" in factors_df.columns and "score" in factors_df.columns and (factors_df["factor"] == "Pattern Quality").any()
                    else 0.0
                ),
                "signal_agreement": float(
                    abs(
                        pd.to_numeric(factors_df.loc[factors_df["factor"] == "Signal Agreement", "raw"], errors="coerce")
                        .fillna(0.0)
                        .iloc[0]
                    )
                    if not factors_df.empty and "factor" in factors_df.columns and "raw" in factors_df.columns and (factors_df["factor"] == "Signal Agreement").any()
                    else 0.0
                ),
                "volume_ratio": float(reasoning.get("volume_ratio", 1.0) or 1.0),
            }
            render_execution_debug(signal_debug)

            try:
                live_or_last = float(stream.latest.price if stream.latest.price > 0 else candles["close"].iloc[-1])
                composite = float(reasoning.get("composite", 0.0) or 0.0)
                preview_side = "buy" if composite >= 0 else "sell"
                sim_sl = float(st.session_state.get("sim_sl", live_or_last * 0.985))
                preview_stop = sim_sl if preview_side == "buy" else max(live_or_last * 1.005, live_or_last + 1.0)

                open_positions_payload: list[dict[str, float | str]] = []
                if not trades.empty and {"symbol", "side", "quantity", "entry_price"}.issubset(trades.columns):
                    for _, row in trades.iterrows():
                        symbol = str(row.get("symbol", "") or "").strip()
                        side = str(row.get("side", "buy") or "buy").strip().lower()
                        quantity = float(pd.to_numeric(row.get("quantity", 0.0), errors="coerce") or 0.0)
                        entry_price = float(pd.to_numeric(row.get("entry_price", live_or_last), errors="coerce") or live_or_last)
                        current_price = float(pd.to_numeric(row.get("current_price", entry_price), errors="coerce") or entry_price)
                        if symbol and quantity > 0:
                            open_positions_payload.append(
                                {
                                    "symbol": symbol,
                                    "side": side,
                                    "quantity": quantity,
                                    "entry_price": entry_price,
                                    "current_price": current_price,
                                    "leverage": 2.0,
                                }
                            )

                preview_payload = {
                    "equity": None,
                    "collateral_balances": [
                        {
                            "asset": asset,
                            "amount": max(collateral_total_usd / max(1, len(collateral_assets)), 1.0),
                            "usd_price": 1.0,
                            "haircut_pct": 0.0,
                        }
                        for asset in collateral_assets
                    ],
                    "symbol_collateral_map": {preview_symbol: preview_bucket_asset},
                    "collateral_bucket_exposure_limits": {preview_bucket_asset: preview_bucket_limit},
                    "trade": {
                        "symbol": preview_symbol,
                        "side": preview_side,
                        "entry_price": live_or_last,
                        "stop_price": preview_stop,
                        "quantity": preview_quantity,
                        "leverage": 2.0,
                    },
                    "open_positions": open_positions_payload,
                }
                risk_preview_resp = get_risk_preview(api_url, preview_payload)
                bucket_pct = float(risk_preview_resp.get("collateral_bucket_exposure_pct", 0.0) or 0.0)
                bucket_limit = float(risk_preview_resp.get("collateral_bucket_limit_pct", 0.0) or 0.0)
                bucket_asset = str(risk_preview_resp.get("trade_collateral_asset", "N/A") or "N/A")
                st.markdown(
                    f"**Collateral Bucket ({preview_symbol}):** {bucket_asset} | "
                    f"Utilization={bucket_pct * 100:.2f}% / Limit={bucket_limit * 100:.2f}%"
                )
            except (ApiContractError, ValueError, TypeError, KeyError):
                st.caption("Collateral bucket preview unavailable.")

    with right:
        st.subheader("Bot Status")
        if worker_status:
            running = bool(worker_status.get("is_running", False))
            state = "RUNNING" if running else "STOPPED"
            st.caption(
                f"{state} | signals={int(worker_status.get('signal_count', 0) or 0)} "
                f"| executions={int(worker_status.get('execution_count', 0) or 0)}"
            )
            decision_reason = str(worker_status.get("last_decision_reason", "") or "").strip()
            if decision_reason:
                st.caption(f"Last decision: {decision_reason}")
            if decision_reason.startswith("entry_gate_failed") and entry_gate_snapshot:
                gate_labels = {
                    "confidence_gate": "confidence",
                    "conviction_gate": "conviction",
                    "trend_gate": "trend",
                    "vol_gate": "volatility",
                    "pattern_gate": "pattern",
                    "agreement_gate": "agreement",
                    "direction_gate": "direction",
                    "volume_gate": "volume",
                }
                failed_gates = [
                    gate_labels.get(key, key)
                    for key, value in entry_gate_snapshot.items()
                    if key.endswith("_gate") and isinstance(value, bool) and not value
                ]
                snapshot_conf = pd.to_numeric(entry_gate_snapshot.get("confidence_pct"), errors="coerce")
                snapshot_comp = pd.to_numeric(entry_gate_snapshot.get("composite"), errors="coerce")
                details: list[str] = []
                if pd.notna(snapshot_conf):
                    details.append(f"conf={float(snapshot_conf):.2f}%")
                if pd.notna(snapshot_comp):
                    details.append(f"composite={float(snapshot_comp):.3f}")
                if details:
                    st.caption(f"Entry gate snapshot: {' | '.join(details)}")
                if failed_gates:
                    st.caption(f"Failed gates: {', '.join(failed_gates)}")
                # Show new gate info from snapshot
                vol_ratio_snap = pd.to_numeric(entry_gate_snapshot.get("volume_ratio"), errors="coerce")
                if pd.notna(vol_ratio_snap):
                    details.append(f"vol_ratio={float(vol_ratio_snap):.2f}")
                candle_patterns_snap = entry_gate_snapshot.get("candle_patterns", [])
                if isinstance(candle_patterns_snap, list) and candle_patterns_snap:
                    st.caption(f"Candle patterns: {', '.join(str(p) for p in candle_patterns_snap)}")
                htf_agrees_snap = entry_gate_snapshot.get("htf_agrees")
                if htf_agrees_snap is not None:
                    htf_tf = entry_gate_snapshot.get("htf_timeframe", "4h")
                    htf_trend_snap = entry_gate_snapshot.get("htf_trend", 0.0)
                    htf_label = "agrees" if htf_agrees_snap else "DISAGREES"
                    st.caption(f"HTF ({htf_tf}): {htf_label} | trend={float(htf_trend_snap):.4f}")
        else:
            st.caption("Worker status unavailable.")

        st.subheader("Active Trades")
        if trades.empty:
            st.info("No active trades.")
        else:
            st.dataframe(trades, use_container_width=True, hide_index=True)

        st.subheader("Open Orders")
        if open_orders.empty:
            st.info("No open orders.")
        else:
            st.dataframe(open_orders, use_container_width=True, hide_index=True)


def render_portfolio(api_url: str) -> None:
    st.subheader("Portfolio Overview")
    try:
        pf = get_portfolio(api_url)
    except ApiContractError as e:
        st.error(str(e))
        st.stop()

    if pf.empty:
        equity_text = ""
        try:
            metrics = get_metrics(api_url)
            equity = float(metrics.get("total_equity", 0.0) or 0.0)
            if equity > 0:
                equity_text = f" Current equity: ${equity:,.2f}."
        except ApiContractError:
            pass
        st.info(f"Connected — no open positions.{equity_text}")
        return

    c1, c2 = st.columns([2, 1.5])
    with c1:
        st.dataframe(pf, use_container_width=True, hide_index=True)
    with c2:
        if {"symbol", "weight_pct"}.issubset(pf.columns):
            pie = go.Figure(go.Pie(labels=pf["symbol"], values=pf["weight_pct"], hole=0.45))
            pie.update_layout(template="plotly_dark", height=360, margin=dict(l=8, r=8, t=24, b=8))
            st.plotly_chart(pie, use_container_width=True)

    st.caption("Includes notional, exposure, and per-asset risk contribution.")


def render_backtesting(api_url: str) -> None:
    import requests as _requests
    st.subheader("Backtesting Dashboard")

    # ── Controls ──────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    symbol      = col1.text_input("Symbol",        "PF_XBTUSD")
    days        = col2.slider("History Days",       7, 90, 30)
    window_days = col3.slider("Window Days",        3, 14, 7)
    initial_eq  = col4.number_input("Initial Equity ($)", 100.0, 100000.0, 5000.0, 100.0)

    use_gate_logic = st.checkbox(
        "Use live gate logic (walk-forward)",
        value=True,
        help="Runs the same entry gates as the live worker. Slower but realistic.",
    )

    run_clicked = st.button("▶ Run Backtest", type="primary", use_container_width=True)

    if run_clicked:
        with st.spinner("Running backtest..."):
            effective_url = str(api_url).strip().rstrip("/")
            if effective_url in {"all-in-one", "direct"}:
                effective_url = "http://127.0.0.1:8000"

            if use_gate_logic:
                try:
                    resp = _requests.post(
                        f"{effective_url}/backtest/run",
                        params={
                            "symbol":         symbol,
                            "days":           days,
                            "window_days":    window_days,
                            "initial_equity": initial_eq,
                        },
                        timeout=120,
                    )
                    if resp.status_code == 200:
                        st.session_state["bt_result"] = resp.json()
                        st.session_state["bt_mode"]   = "walk_forward"
                    else:
                        st.warning(f"Walk-forward backtest returned HTTP {resp.status_code} — falling back to SMA crossover.")
                        use_gate_logic = False
                except Exception as exc:
                    st.warning(f"Walk-forward backtest failed ({exc}) — falling back to SMA crossover.")
                    use_gate_logic = False

            if not use_gate_logic:
                try:
                    bt = get_backtest(api_url, lookback_days=days)
                    st.session_state["bt_result"] = bt
                    st.session_state["bt_mode"]   = "sma_crossover"
                except ApiContractError as exc:
                    st.error(str(exc))
                    return

    result = st.session_state.get("bt_result")
    mode   = st.session_state.get("bt_mode", "sma_crossover")

    if result is None:
        st.info("Configure parameters above and click **Run Backtest**.")
        return

    # ── Walk-forward results ───────────────────────────────────────────────
    if mode == "walk_forward":
        agg = result.get("aggregate", {})
        signal      = result.get("signal", "red")
        signal_text = result.get("signal_text", "")

        if signal == "green":
            st.success(signal_text)
        elif signal == "yellow":
            st.warning(signal_text)
        else:
            st.error(signal_text)

        st.caption(
            f"Walk-forward | {result.get('windows', 0)} windows | "
            f"{result.get('total_trades', 0)} total trades"
        )

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Net PnL",      f"${agg.get('net_pnl', 0):,.2f}")
        m2.metric("Mean Sharpe",  f"{agg.get('mean_sharpe', 0):.3f}")
        m3.metric("Win Rate",     f"{agg.get('mean_win_rate', 0):.1f}%")
        m4.metric("Max Drawdown", f"{agg.get('mean_max_dd', 0):.1f}%")
        m5.metric("Profit Factor",f"{agg.get('mean_pf', 0):.2f}")
        m6.metric("Total Fees",   f"${agg.get('total_fees', 0):,.4f}")

        windows = result.get("windows_detail", [])
        if windows:
            wdf = pd.DataFrame(windows)
            wdf["start"] = pd.to_datetime(wdf["start"])

            tab1, tab2, tab3 = st.tabs(["Net PnL per Window", "Sharpe per Window", "Win Rate per Window"])
            with tab1:
                st.bar_chart(wdf.set_index("start")["net_pnl"])
            with tab2:
                st.line_chart(wdf.set_index("start")["sharpe"])
            with tab3:
                st.line_chart(wdf.set_index("start")["win_rate"])

            wdf["equity_curve"] = wdf["net_pnl"].cumsum() + initial_eq
            st.subheader("Cumulative Equity Curve")
            st.line_chart(wdf.set_index("start")["equity_curve"])

            st.subheader("Window Detail")
            st.dataframe(
                wdf[["start", "trades", "net_pnl", "sharpe", "win_rate", "max_dd", "pf"]].rename(
                    columns={"net_pnl": "Net PnL", "win_rate": "Win Rate %",
                             "max_dd": "Max DD %", "pf": "Profit Factor"}
                ),
                use_container_width=True,
                hide_index=True,
            )
        return

    # ── SMA crossover fallback results ────────────────────────────────────
    stats        = result.get("stats", {})
    analytics    = result.get("analytics", {})
    profit_factor    = float(analytics.get("profit_factor",     stats.get("profit_factor",    0)) or 0)
    total_return_pct = float(analytics.get("total_return_pct",  stats.get("total_return_pct", 0)) or 0)

    st.caption("SMA-crossover simulation (gate logic backtest unavailable)")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Net PnL",       f"${float(stats.get('net_pnl', 0)):,.2f}")
    c2.metric("Return %",      f"{total_return_pct:.2f}%")
    c3.metric("Win Rate",      f"{float(stats.get('win_rate', 0)):.2f}%")
    c4.metric("Sharpe",        f"{float(stats.get('sharpe', 0)):.2f}")
    c5.metric("Max Drawdown",  f"{float(stats.get('max_drawdown', 0)):.2f}%")
    c6.metric("Profit Factor", f"{profit_factor:.2f}")
    c7.metric("Trades",        f"{int(stats.get('trades', 0))}")

    curve = pd.DataFrame(result.get("equity_curve", []))
    if not curve.empty and "t" in curve.columns and "equity" in curve.columns:
        fig = go.Figure(go.Scatter(x=curve["t"], y=curve["equity"], mode="lines", name="Equity Curve"))
        fig.update_layout(template="plotly_dark", height=420, margin=dict(l=8, r=8, t=24, b=8))
        st.plotly_chart(fig, use_container_width=True)

    monthly = result.get("monthly_performance", [])
    if monthly:
        mdf = pd.DataFrame(monthly)
        st.subheader("Monthly Performance")
        st.dataframe(mdf, use_container_width=True, hide_index=True)
