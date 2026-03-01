import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

def apply_chart_theme(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        plot_bgcolor="#0D131C",
        paper_bgcolor="#06090F",
        font=dict(color="#E8EEF7"),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0,
                    bgcolor="rgba(0,0,0,0)", bordercolor="rgba(255,255,255,.08)", borderwidth=1),
        margin=dict(l=10, r=10, t=35, b=10),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,.08)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,.08)")
    return fig

def _resample_if_too_dense(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 120:
        return df
    d = df.sort_values("timestamp").copy()
    med_dt = d["timestamp"].diff().dropna().median()
    # If sub-second / tick-like feed, resample to 1-second candles
    if pd.notna(med_dt) and med_dt < pd.Timedelta(seconds=1):
        out = (
            d.set_index("timestamp")
             .resample("1S")
             .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
             .dropna()
             .reset_index()
        )
        return out
    return d.tail(300)

def render_advanced_chart(df: pd.DataFrame, signals_df: pd.DataFrame):
    if df.empty:
        st.warning("Waiting for market data feed...")
        return

    df = df.copy()

    # Parse timestamp early
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Chart data missing columns: {missing}")
        return

    df = df.dropna(subset=required)

    # Basic validity
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df[(df["high"] >= df[["open", "close", "low"]].max(axis=1))]
    df = df[(df["low"] <= df[["open", "close", "high"]].min(axis=1))]

    # Remove absurd timestamps (prevents x-axis compression)
    now = pd.Timestamp.utcnow().tz_localize("UTC") if pd.Timestamp.utcnow().tzinfo is None else pd.Timestamp.utcnow()
    df = df[(df["timestamp"] > now - pd.Timedelta(days=30)) & (df["timestamp"] < now + pd.Timedelta(minutes=5))]

    # Remove price outliers (prevents y-axis flattening)
    q1 = df["close"].quantile(0.01)
    q99 = df["close"].quantile(0.99)
    if pd.notna(q1) and pd.notna(q99) and q99 > q1:
        df = df[(df["low"] >= q1 * 0.98) & (df["high"] <= q99 * 1.02)]

    df = df.sort_values("timestamp")
    df = _resample_if_too_dense(df)

    if df.empty:
        st.warning("No valid candle data after cleanup.")
        return

    # Build candle range first
    y_min = float(df["low"].min())
    y_max = float(df["high"].max())

    # Indicators
    df["SMA_20"] = df["close"].rolling(window=20).mean()
    df["Upper_BB"] = df["SMA_20"] + (df["close"].rolling(window=20).std() * 2)
    df["Lower_BB"] = df["SMA_20"] - (df["close"].rolling(window=20).std() * 2)

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=("Price Action & Signals", "Volume"),
        row_heights=[0.82, 0.18],
    )

    fig.add_trace(go.Candlestick(
        x=df["timestamp"],
        open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name="OHLC"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["SMA_20"], line=dict(color="#ffb020", width=1), name="SMA 20"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["Upper_BB"], line=dict(color="gray", width=1, dash="dot"), name="Upper BB"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["Lower_BB"], line=dict(color="gray", width=1, dash="dot"), name="Lower BB"), row=1, col=1)

    # Sanitize signals so they can't destroy y-axis autoscale
    if not signals_df.empty:
        s = signals_df.copy()
        if "timestamp" in s.columns:
            s["timestamp"] = pd.to_datetime(s["timestamp"], errors="coerce", utc=True)
        if "price" in s.columns:
            s["price"] = pd.to_numeric(s["price"], errors="coerce")
        else:
            s["price"] = np.nan

        s = s.dropna(subset=["timestamp", "price"])
        s = s[(s["price"] >= y_min * 0.8) & (s["price"] <= y_max * 1.2)]

        if {"side", "timestamp", "price"}.issubset(s.columns):
            buys = s[s["side"] == "buy"]
            sells = s[s["side"] == "sell"]
            if not buys.empty:
                fig.add_trace(go.Scatter(
                    x=buys["timestamp"], y=buys["price"],
                    mode="markers", marker=dict(symbol="triangle-up", size=12, color="#00D084"),
                    name="Buy Signal"
                ), row=1, col=1)
            if not sells.empty:
                fig.add_trace(go.Scatter(
                    x=sells["timestamp"], y=sells["price"],
                    mode="markers", marker=dict(symbol="triangle-down", size=12, color="#FF4D4F"),
                    name="Sell Signal"
                ), row=1, col=1)

    colors = ["#FF4D4F" if r.open > r.close else "#00D084" for _, r in df.iterrows()]
    fig.add_trace(go.Bar(x=df["timestamp"], y=df["volume"], marker_color=colors, name="Volume"), row=2, col=1)

    fig = apply_chart_theme(fig)
    fig.update_layout(height=760)
    fig.update_xaxes(
        tickformat="%H:%M:%S",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikethickness=1
    )

    # Force readable zoom even when spread is tiny
    mid = float(df["close"].median())
    spread_ratio = (y_max - y_min) / mid if mid > 0 else 0.0
    if spread_ratio < 0.002:   # < 0.2%
        pad = max(mid * 0.005, 5.0)  # ±0.5% min pad
    else:
        pad = (y_max - y_min) * 0.08

    fig.update_yaxes(range=[y_min - pad, y_max + pad], row=1, col=1)

    st.plotly_chart(fig, use_container_width=True)