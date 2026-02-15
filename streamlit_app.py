import streamlit as st
import ccxt  # type: ignore
import pandas as pd
from datetime import datetime
import time
from typing import Any, Dict, Optional, Literal

from engine.futures_adapter import connect_kraken
from engine.execution_manager import ExecutionManager
from engine.positions import PositionManager
from engine.risk import RiskManager
from engine.validation import validate_order_params
from engine.execution import execute_order, place_stop_loss_order, place_take_profit_order

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
            for key in ["exchange", "balance", "server_time", "markets", "symbols"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

# ---------- CONNECTION ----------
if connect:
    if not api_key or not api_secret:
        st.error("❌ Please enter both API key and secret")
    elif kraken_env is None:
        st.error("Environment not selected. Please choose 'Spot' or 'Futures (Demo)'.")
    else:
        exchange, balance, server_time, markets, error = connect_kraken(
            api_key,
            api_secret,
            kraken_env
        )

        if exchange:
            st.session_state["exchange"] = exchange
            st.session_state["balance"] = balance
            st.session_state["server_time"] = server_time
            st.session_state["markets"] = markets
            st.session_state["symbols"] = exchange.symbols  # Store dynamic symbols
            st.session_state["connected_at"] = datetime.now()
            st.success(f"✅ Connected to Kraken {kraken_env} successfully")
            st.rerun()
        else:
            st.error(f"❌ Connection failed: {error}")

# ---------- DASHBOARD ----------
if "exchange" in st.session_state:
    connected_duration = datetime.now() - st.session_state.get("connected_at", datetime.now())
    st.success(f"🟢 Connected (Session: {connected_duration.seconds // 60}m {connected_duration.seconds % 60}s)")
    
    if st.button("🔄 Refresh Data"):
        try:
            st.session_state["balance"] = st.session_state["exchange"].fetch_balance()
            st.session_state["server_time"] = st.session_state["exchange"].fetch_time()
            st.rerun()
        except Exception as e:
            st.error(f"Failed to refresh: {str(e)}")
    
    st.subheader("📊 Account Overview")

    server_dt = datetime.fromtimestamp(st.session_state["server_time"] / 1000)
    local_dt = datetime.now()
    st.write(f"🕒 Kraken Server Time: **{server_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC**")
    st.write(f"🕒 Local Time: **{local_dt.strftime('%Y-%m-%d %H:%M:%S')}**")

    balances = st.session_state["balance"]["total"]
    free_balances = st.session_state["balance"].get("free", {})
    used_balances = st.session_state["balance"].get("used", {})
    
    df_balances = pd.DataFrame({
        "Asset": balances.keys(),
        "Total": balances.values(),
        "Free": [free_balances.get(asset, 0) for asset in balances.keys()],
        "Used": [used_balances.get(asset, 0) for asset in balances.keys()]
    })
    
    df_balances = (
        df_balances[df_balances["Total"] > 0]
        .sort_values("Total", ascending=False)
        .reset_index(drop=True)
    )

    if not df_balances.empty:
        st.dataframe(df_balances, use_container_width=True)
        if "USDT" in balances and balances["USDT"] > 0:
            st.metric("💰 USDT Balance", f"${balances['USDT']:,.2f}")
    else:
        st.info("No balances found")

    st.divider()

    # ---------- DYNAMIC MARKET DATA SECTION ----------
    st.subheader("📈 Live Market Data")
    
    # Get available symbols from exchange
    available_symbols = st.session_state.get("symbols", [])
    
    if not available_symbols:
        st.warning("⚠️ No trading pairs available")
    else:
        col1, col2 = st.columns([3, 1])
        with col1:
            trading_pair = st.selectbox(
                "Select Trading Pair",
                available_symbols,
                index=0
            )
        with col2:
            auto_refresh = st.checkbox("Auto-refresh", value=False)
        
        try:
            ticker = st.session_state["exchange"].fetch_ticker(trading_pair)
            current_price = ticker['last']
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Last Price", f"${current_price:,.2f}")
            col2.metric("24h High", f"${ticker['high']:,.2f}")
            col3.metric("24h Low", f"${ticker['low']:,.2f}")
            
            change_pct = ticker.get('percentage')
            if change_pct is not None:
                col4.metric("24h Change", f"{change_pct:.2f}%", delta=f"{change_pct:.2f}%")
            
            base_asset = trading_pair.split('/')[0] if trading_pair else "?"
            st.write(f"**Volume (24h):** {ticker.get('baseVolume', 0):,.2f} {base_asset}")
            st.write(f"**Bid:** ${ticker.get('bid', 0):,.2f} | **Ask:** ${ticker.get('ask', 0):,.2f}")
            
            if auto_refresh:
                time.sleep(5)
                st.rerun()
                
        except Exception as e:
            st.error(f"Failed to fetch market data: {str(e)}")
            current_price = 0

    st.divider()
    
    # ---------- DYNAMIC ORDER PLACEMENT ----------
    st.subheader("⚡ Place Order with Stop Loss & Take Profit")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        order_symbol = st.selectbox(
            "Symbol",
            available_symbols,
            index=0,
            key="order_symbol"
        )
    
    with col2:
        order_side = st.selectbox("Side", ["buy", "sell"])
    
    with col3:
        order_type = st.selectbox("Type", ["market", "limit"])
    
    with col4:
        order_amount = st.number_input("Amount", min_value=0.0, step=0.001, format="%.6f")
    
    order_price = None
    if order_type == "limit":
        order_price = st.number_input(
            "Price",
            min_value=0.0,
            value=float(current_price) if current_price > 0 else 0.0,
            step=0.01,
            format="%.2f"
        )
    else:
        order_price = current_price
    
    if order_symbol in st.session_state["markets"]:
        market = st.session_state["markets"][order_symbol]
        st.caption(f"Min Amount: {market['limits']['amount']['min']} | Min Cost: ${market['limits']['cost']['min']}")
    
    st.divider()
    
    # Stop Loss & Take Profit settings
    st.subheader("🛡️ Risk Management")
    
    col1, col2 = st.columns(2)
    
    with col1:
        use_stop_loss = st.checkbox("Enable Stop Loss", value=True)
        if use_stop_loss:
            sl_method = st.radio("Stop Loss Method", ["Percentage", "Price"], horizontal=True, key="sl_method")
            if sl_method == "Percentage":
                sl_percentage = st.number_input("Stop Loss %", min_value=0.1, max_value=50.0, value=2.0, step=0.1)
                if order_price and order_price > 0:
                    if order_side == "buy":
                        stop_loss_price = order_price * (1 - sl_percentage / 100)
                    else:
                        stop_loss_price = order_price * (1 + sl_percentage / 100)
                    st.info(f"Stop Loss Price: **${stop_loss_price:,.2f}**")
                else:
                    stop_loss_price = None
            else:
                stop_loss_price = st.number_input("Stop Loss Price", min_value=0.0, step=0.01, format="%.2f")
        else:
            stop_loss_price = None
    
    with col2:
        use_take_profit = st.checkbox("Enable Take Profit", value=False)
        if use_take_profit:
            tp_method = st.radio("Take Profit Method", ["Percentage", "Price"], horizontal=True, key="tp_method")
            if tp_method == "Percentage":
                tp_percentage = st.number_input("Take Profit %", min_value=0.1, max_value=100.0, value=5.0, step=0.1)
                if order_price and order_price > 0:
                    if order_side == "buy":
                        take_profit_price = order_price * (1 + tp_percentage / 100)
                    else:
                        take_profit_price = order_price * (1 - tp_percentage / 100)
                    st.info(f"Take Profit Price: **${take_profit_price:,.2f}**")
                else:
                    take_profit_price = None
            else:
                take_profit_price = st.number_input("Take Profit Price", min_value=0.0, step=0.01, format="%.2f")
        else:
            take_profit_price = None
    
    # Display risk/reward
    if stop_loss_price and take_profit_price and order_price:
        risk_amount = abs(order_price - stop_loss_price) * order_amount
        reward_amount = abs(take_profit_price - order_price) * order_amount
        rr_ratio = reward_amount / risk_amount if risk_amount > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Risk Amount", f"${risk_amount:,.2f}")
        col2.metric("Reward Amount", f"${reward_amount:,.2f}")
        col3.metric("R:R Ratio", f"1:{rr_ratio:.2f}")
    
    st.divider()
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🚀 Place Order", type="primary", use_container_width=True):
            execution_mgr: ExecutionManager = st.session_state["execution_mgr"]
            result = execution_mgr.execute_order(
                order_symbol,
                order_side,
                order_type,
                order_amount,
                order_price
            )
                
            if result["success"]:
                st.success(f"✅ Main order placed successfully!")
                st.json({
                    "Order ID": result["order_id"],
                    "Symbol": result["symbol"],
                    "Side": result["side"],
                    "Type": result["type"],
                    "Amount": result["amount"],
                    "Price": result["price"],
                    "Status": result["status"]
                })
                
                if "order_history" not in st.session_state:
                    st.session_state["order_history"] = []
                st.session_state["order_history"].append(result)
                
                # ---------- CHECK POSITIONS BEFORE SL/TP ----------
                position_mgr: PositionManager = st.session_state["position_mgr"]
                position_result = position_mgr.get_position_for_symbol(order_symbol)
                
                if position_result["success"] and position_result["has_position"]:
                    position = position_result["position"]
                    st.info(f"📍 Found open position: {position.get('side').upper()} {position.get('contracts')} contracts @ ${position.get('markPrice', 'N/A')}")
                    
                    # Validate SL/TP against position
                    if use_stop_loss and stop_loss_price:
                        validation = validate_sl_tp_for_position(
                            position,
                            order_price,
                            stop_loss_price,
                            take_profit_price if use_take_profit else order_price,
                            order_side
                        )
                        
                        if not validation["valid"]:
                            st.error(f"❌ Stop Loss Validation Failed:")
                            for err in validation["errors"]:
                                st.error(f"  • {err}")
                        else:
                            for warning in validation["warnings"]:
                                st.warning(f"⚠️ {warning}")
                        
                    # Place stop loss
                    if use_stop_loss and stop_loss_price:
                        validation = validate_sl_tp_for_position(
                            position,
                            order_price,
                            stop_loss_price,
                            take_profit_price if use_take_profit else order_price,
                            order_side
                        )
                        
                        if validation["valid"]:
                            with st.spinner("Placing stop loss..."):
                                sl_result = place_stop_loss_order(
                                    st.session_state["exchange"],
                                    st.session_state["markets"],
                                    order_symbol,
                                    order_side,
                                    order_amount,
                                    stop_loss_price
                                )
                                
                            if sl_result["success"]:
                                st.success(f"✅ Stop loss placed at ${stop_loss_price:,.2f}")
                                st.session_state["order_history"].append(sl_result)
                            else:
                                st.warning(f"⚠️ Stop loss failed: {sl_result['error']}")
                        else:
                            st.error(f"❌ Stop loss rejected: {validation['errors'][0]}")
                        
                    # Place take profit
                    if use_take_profit and take_profit_price and order_side and order_symbol:
                        validation = validate_sl_tp_for_position(
                            position,
                            order_price,
                            stop_loss_price if use_stop_loss else order_price,
                            take_profit_price,
                            order_side
                        )
                        
                        if validation["valid"]:
                            with st.spinner("Placing take profit..."):
                                tp_result = place_take_profit_order(
                                    st.session_state["exchange"],
                                    st.session_state["markets"],
                                    order_symbol,
                                    order_side,
                                    order_amount,
                                    take_profit_price
                                )
                                
                            if tp_result["success"]:
                                st.success(f"✅ Take profit placed at ${take_profit_price:,.2f}")
                                st.session_state["order_history"].append(tp_result)
                            else:
                                st.warning(f"⚠️ Take profit failed: {tp_result['error']}")
                        else:
                            st.error(f"❌ Take profit rejected: {validation['errors'][0]}")
                    else:
                        st.warning(f"⚠️ No open position found for {order_symbol}. SL/TP may not be placed.")
                    
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"❌ Order failed: {result['error']}")
    
    with col2:
        if st.button("🧪 Validate Order", use_container_width=True):
            is_valid, result = validate_order_params(
                st.session_state["exchange"],
                st.session_state["markets"],
                order_symbol,
                order_side,
                order_amount,
                order_price
            )
            if is_valid:
                st.success(f"✅ Order valid - Amount: {result['amount']}, Price: {result['price']}")
            else:
                st.error(f"❌ {result}")
    
    with col3:
        if st.button("🔄 Reset Form", use_container_width=True):
            st.rerun()

    st.divider()
    
    # Open orders section
    st.subheader("📋 Open Orders")
    try:
        open_orders = st.session_state["exchange"].fetch_open_orders()
        if open_orders:
            df_orders = pd.DataFrame(open_orders)
            st.dataframe(
                df_orders[["symbol", "side", "type", "price", "amount", "status", "datetime"]],
                use_container_width=True
            )
            
            if st.checkbox("Show Cancel Options"):
                order_id = st.text_input("Order ID to cancel")
                order_symbol_cancel = st.selectbox(
                    "Order Symbol",
                    available_symbols,
                    key="cancel_symbol"
                )
                if st.button("❌ Cancel Order"):
                    try:
                        st.session_state["exchange"].cancel_order(order_id, order_symbol_cancel)
                        st.success(f"✅ Order {order_id} cancelled")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Failed to cancel: {str(e)}")
        else:
            st.info("No open orders")
    except Exception as e:
        st.warning(f"Could not fetch open orders: {str(e)}")

    st.divider()
    
    # Order history section
    if "order_history" in st.session_state and st.session_state["order_history"]:
        st.subheader("📜 Order History (This Session)")
        df_history = pd.DataFrame(st.session_state["order_history"])
        display_cols = [
            col for col in ["order_id", "symbol", "side", "type", "amount", "price", 
                          "stop_price", "limit_price", "status", "datetime"]
            if col in df_history.columns
        ]
        st.dataframe(df_history[display_cols], use_container_width=True)

else:
    st.info("🔒 Connect your Kraken account to view live data")
    
    with st.expander("ℹ️ How to get Kraken API credentials"):
        st.markdown("""
        1. Log in to your Kraken account
        2. Go to **Settings** → **API**
        3. Click **Generate New Key**
        4. Set permissions:
           - ✅ Query Funds
           - ✅ Query Open Orders & Trades
           - ✅ Query Closed Orders & Trades
           - ✅ Create & Modify Orders (for trading)
           - ❌ **DO NOT enable withdrawals**
        5. Copy your API Key and Secret
        6. Paste them in the sidebar
        """)

st.divider()
st.warning("⚠️ Never enable withdrawals on API keys | Keep credentials secure | Use at your own risk")
st.caption(f"Dashboard updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")