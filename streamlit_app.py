import streamlit as st
from datetime import datetime
import time
import sys
sys.path.append(".")

# INITIALIZE SESSION STATE FIRST - BEFORE EVERYTHING
if "exchange" not in st.session_state:
    st.session_state.exchange = None
if "exchange_connected" not in st.session_state:
    st.session_state.exchange_connected = False
if "server_time" not in st.session_state:
    st.session_state.server_time = None
if "balance" not in st.session_state:
    st.session_state.balance = None
if "markets" not in st.session_state:
    st.session_state.markets = None
if "execution_mgr" not in st.session_state:
    st.session_state.execution_mgr = None
if "connected_at" not in st.session_state:
    st.session_state.connected_at = None

# NOW import other modules
from engine.futures_adapter import connect_kraken, test_connection
from engine.execution_manager import ExecutionManager
from engine.positions import PositionManager
from engine.risk import RiskManager
from engine.validation import validate_order_params
from engine.execution import execute_order, place_stop_loss_order, place_take_profit_order
import pandas as pd

st.set_page_config(
    page_title="AI Trading Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 AI Trading Dashboard — Kraken Live")

# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("🔐 Exchange Settings")

    kraken_env = st.selectbox(
        "Kraken Environment",
        ["Spot", "Futures (Demo)"]
    )

    if kraken_env == "Futures (Demo)":
        key_label = "Kraken Futures Public Key"
        secret_label = "Kraken Futures Private Key"
        key_help = "Futures demo public key (no withdrawals)"
        secret_help = "Futures demo private key"
    else:
        key_label = "Kraken API Key"
        secret_label = "Kraken API Secret"
        key_help = "API key with trading enabled (NO withdrawals)"
        secret_help = "API secret with trading enabled"

    api_key = st.text_input(key_label, type="password", help=key_help)
    api_secret = st.text_input(secret_label, type="password", help=secret_help)

    connect = st.button("🔌 Connect to Kraken")

    if "exchange" in st.session_state:
        if st.button("🔌 Disconnect"):
            for key in ["exchange", "balance", "server_time", "markets", "symbols", "connected_at"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.session_state.exchange_connected = False
            st.rerun()

# ---------- CONNECTION ----------
if connect:
    if not api_key or not api_secret:
        st.error("❌ Please enter both API key and secret")
    else:
        with st.spinner("Connecting to Kraken..."):
            exchange, balance, server_time, markets, error = connect_kraken(
                api_key,
                api_secret,
                kraken_env
            )
        
        if error == "":
            st.success("✅ Connected to Kraken!")
            st.session_state.exchange = exchange
            st.session_state.server_time = server_time
            st.session_state.balance = balance
            st.session_state.markets = markets
            st.session_state.exchange_connected = True
            st.session_state.connected_at = datetime.now()
            st.rerun()
        else:
            st.error(f"❌ {error}")
            st.session_state.exchange_connected = False

# Ensure exchange_symbols is defined
exchange = st.session_state.get("exchange")
exchange_symbols = exchange.symbols if exchange else []

# ---------- DASHBOARD ----------
if "exchange" in st.session_state and st.session_state.exchange_connected:
    connected_duration = datetime.now() - st.session_state.get("connected_at", datetime.now())
    st.success(f"🟢 Connected (Session: {connected_duration.seconds // 60}m {connected_duration.seconds % 60}s)")
    
    if st.button("🔄 Refresh Data"):
        try:
            st.session_state["balance"] = st.session_state["exchange"].fetch_balance()
            st.rerun()
        except Exception as e:
            st.error(f"Failed to refresh: {str(e)}")
    
    st.subheader("📊 Account Overview")

    if st.session_state.server_time:
        col1, col2 = st.columns(2)
        
        with col1:
            server_dt = datetime.fromtimestamp(st.session_state.server_time / 1000)
            st.metric("Kraken Server Time", server_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))
        
        with col2:
            local_dt = datetime.now()
            st.metric("Local Time", local_dt.strftime("%Y-%m-%d %H:%M:%S"))
        
        if st.session_state.balance:
            st.info(f"Total Balance: {st.session_state.balance}")
        else:
            st.info("No balances found")

    st.divider()

    # ---------- LIVE MARKET DATA ----------
    st.subheader("📈 Live Market Data")
    if exchange_symbols and len(exchange_symbols) > 0:
        col1, col2 = st.columns([3, 1])
        with col1:
            trading_pair = st.selectbox("Select Trading Pair", exchange_symbols, index=0, key="market_symbol")
        with col2:
            auto_refresh = st.checkbox("Auto-refresh", value=False)
        
        try:
            if not trading_pair or trading_pair == "None":
                st.warning("⚠️ Invalid trading pair selected")
            else:
                ticker = st.session_state["exchange"].fetch_ticker(trading_pair)
                
                # Kraken Futures: use markPrice from info, fallback to bid/ask
                current_price = None
                if ticker.get('info') and ticker['info'].get('markPrice'):
                    current_price = float(ticker['info']['markPrice'])
                elif ticker.get('bid'):
                    current_price = float(ticker['bid'])
                elif ticker.get('ask'):
                    current_price = float(ticker['ask'])
                else:
                    current_price = 0
            
                if current_price and current_price > 0:
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Mark Price", f"${current_price:.4f}")
                    col2.metric("Bid", f"${ticker.get('bid', 0):.4f}")
                    col3.metric("Ask", f"${ticker.get('ask', 0):.4f}")
                    col4.metric("Spread", f"${(ticker.get('ask', 0) - ticker.get('bid', 0)):.4f}")
                    
                    base_asset = trading_pair.split('/')[0] if '/' in trading_pair else trading_pair.split(':')[0]
                    st.write(f"**24h Volume:** {ticker.get('baseVolume', 'N/A')} {base_asset}")
                    st.write(f"**Index Price:** ${ticker.get('indexPrice', 'N/A')}")
                else:
                    st.warning("⚠️ No price data available")
            
            if auto_refresh:
                time.sleep(5)
                st.rerun()
                
        except Exception as e:
            st.error(f"Failed to fetch market data: {str(e)}")
    else:
        st.warning("⚠️ No trading pairs available - Connect to Kraken first")

    st.divider()

st.subheader("⚡ Place Order with Stop Loss & Take Profit")