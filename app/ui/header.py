import streamlit as st
from app.utils.formatting import format_currency, format_pct

def render_header(status: dict, risk: dict):
    cols = st.columns([2, 1, 1, 1, 1, 1, 1])
    with cols[0]:
        st.markdown(f"### 🐆 **KRAKEN PRO** | {status.get('symbol', 'PI_XBTUSD')}")
        st.caption(f"Strategy: Momentum V1 | Status: {'🟢 ONLINE' if status.get('is_running') else '🔴 OFFLINE'}")
    with cols[1]:
        st.metric("Balance", format_currency(risk.get('account_balance', 0)), delta=format_currency(risk.get('total_pnl', 0)))
    with cols[2]:
        st.metric("Open Pos", int(risk.get('open_positions', 0)))
    with cols[3]:
        st.metric("Session PnL", format_currency(risk.get('total_pnl', 0)), delta=None)
    with cols[4]:
        drawdown = risk.get('drawdown_pct', 0)
        st.metric("Max DD", format_pct(drawdown), delta_color="inverse")
    with cols[5]:
        st.metric("Sig/Exec", f"{status.get('signal_count', 0)}/{status.get('execution_count', 0)}")
    st.markdown("---")