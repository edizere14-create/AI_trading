import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="AI Trading Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 AI Trading Dashboard")

# Sidebar
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("API Key", type="password")
    broker = st.selectbox("Broker", ["Alpaca", "Interactive Brokers", "TD Ameritrade"])
    st.divider()
    st.info("Connect your trading account to get started")

# Main content
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🤖 Strategies", "📜 History"])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Portfolio Value", "$0.00", "0%")
    with col2:
        st.metric("Today's P&L", "$0.00", "0%")
    with col3:
        st.metric("Total Return", "0%", "0%")
    
    st.subheader("Recent Trades")
    st.info("No trades yet. Configure your strategy to get started.")

with tab2:
    st.subheader("Active Trading Strategies")
    strategy = st.selectbox("Select Strategy", ["Mean Reversion", "Momentum", "ML-Based"])
    
    col1, col2 = st.columns(2)
    with col1:
        st.number_input("Position Size (%)", min_value=1, max_value=100, value=10)
    with col2:
        st.number_input("Stop Loss (%)", min_value=1, max_value=20, value=5)
    
    if st.button("Start Strategy"):
        st.success(f"✅ {strategy} strategy activated!")

with tab3:
    st.subheader("Trade History")
    st.dataframe(pd.DataFrame({
        "Time": [],
        "Symbol": [],
        "Action": [],
        "Quantity": [],
        "Price": [],
        "P&L": []
    }))

st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")