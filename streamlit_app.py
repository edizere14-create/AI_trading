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
import plotly.graph_objects as go
from datetime import timedelta

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
                    
                    # NEW: Timeframe selector for charts
                    chart_col1, chart_col2, chart_col3 = st.columns([2, 1, 1])
                    with chart_col1:
                        st.write("**Chart Settings**")
                    with chart_col2:
                        selected_timeframe = st.selectbox(
                            "Timeframe",
                            ["1h", "4h", "1d"],
                            index=0,
                            key="chart_timeframe"
                        )
                    with chart_col3:
                        num_candles = st.number_input(
                            "Candles",
                            min_value=10,
                            max_value=100,
                            value=24,
                            step=5
                        )
                    
                    # Fetch OHLCV data for selected timeframe
                    try:
                        ohlcv = st.session_state["exchange"].fetch_ohlcv(
                            trading_pair,
                            timeframe=selected_timeframe,
                            limit=int(num_candles)
                        )
                        
                        # Create candlestick chart
                        fig = go.Figure(data=[go.Candlestick(
                            x=[datetime.fromtimestamp(candle[0]/1000) for candle in ohlcv],
                            open=[candle[1] for candle in ohlcv],
                            high=[candle[2] for candle in ohlcv],
                            low=[candle[3] for candle in ohlcv],
                            close=[candle[4] for candle in ohlcv],
                            name="OHLC"
                        )])
                        
                        # Add volume bars
                        fig.add_trace(go.Bar(
                            x=[datetime.fromtimestamp(candle[0]/1000) for candle in ohlcv],
                            y=[candle[5] for candle in ohlcv],
                            name="Volume",
                            yaxis="y2",
                            marker_color='rgba(128,128,128,0.5)'
                        ))
                        
                        # Update layout with dual axes
                        fig.update_layout(
                            title=f"{trading_pair} - {selected_timeframe.upper()} Chart ({num_candles} candles)",
                            yaxis_title='Price (USD)',
                            yaxis2=dict(
                                title='Volume',
                                overlaying='y',
                                side='right'
                            ),
                            xaxis_title='Time',
                            template='plotly_dark',
                            height=600,
                            xaxis_rangeslider_visible=False,
                            hovermode='x unified'
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # Display OHLCV stats
                        df_ohlcv = pd.DataFrame(
                            ohlcv,
                            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                        )
                        df_ohlcv['timestamp'] = pd.to_datetime(df_ohlcv['timestamp'], unit='ms')
                        
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("High", f"${df_ohlcv['high'].max():.4f}")
                        col2.metric("Low", f"${df_ohlcv['low'].min():.4f}")
                        col3.metric("Avg Volume", f"{df_ohlcv['volume'].mean():.0f}")
                        col4.metric("Total Volume", f"{df_ohlcv['volume'].sum():.0f}")
                        
                    except Exception as chart_error:
                        st.warning(f"⚠️ Chart data unavailable: {str(chart_error)}")
                        st.error(str(chart_error))
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
    
    # ---------- ORDER PLACEMENT ----------
    st.subheader("⚡ Place Order with Stop Loss & Take Profit")
    
    if not exchange_symbols:
        st.error("❌ No trading pairs available - Connect to Kraken first")
        st.stop()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        selected_symbol = st.selectbox("Symbol", exchange_symbols, key="order_symbol")
        order_symbol = selected_symbol
        
        try:
            ticker = exchange.fetch_ticker(selected_symbol)
            if ticker.get('info') and ticker['info'].get('markPrice'):
                current_price = float(ticker['info']['markPrice'])
            else:
                current_price = float(ticker.get('bid', 0))
        except:
            current_price = 0.0
    
    with col2:
        order_side = st.selectbox("Side", ["buy", "sell"])
    
    with col3:
        order_type = st.selectbox("Type", ["market", "limit"])
    
    with col4:
        # Get minimum amount from market
        min_amount = 1  # Default
        if selected_symbol in st.session_state.get("markets", {}):
            market = st.session_state["markets"][selected_symbol]
            min_amt = market.get('limits', {}).get('amount', {}).get('min')
            if min_amt is not None:
                min_amount = min_amt
        
        order_amount = st.number_input(
            "Amount",
            min_value=float(min_amount),
            step=float(min_amount),
            format="%.1f",
            value=float(min_amount)
        )
    
    order_price = current_price
    
    if order_type == "limit":
        order_price = st.number_input(
            "Price",
            min_value=0.0,
            value=float(current_price) if current_price and current_price > 0 else 0.0,
            step=0.01,
            format="%.4f"
        )
    
    # ENSURE order_price is always a valid number
    if not order_price or order_price <= 0:
        order_price = current_price
    
    if not order_price or order_price <= 0:
        st.error("❌ Unable to determine order price. Check market data.")
        st.stop()

    if selected_symbol in st.session_state.get("markets", {}):
        market = st.session_state["markets"][selected_symbol]
        min_amount = market['limits']['amount'].get('min', 0)
        min_cost = market['limits']['cost'].get('min', 0)
        st.caption(f"Min Amount: {min_amount} | Min Cost: ${min_cost}")
    
    st.divider()
    
    # ---------- RISK MANAGEMENT ----------
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
                    st.info(f"Stop Loss Price: **${stop_loss_price:.4f}**")
                else:
                    stop_loss_price = None
            else:
                stop_loss_price = st.number_input("Stop Loss Price", min_value=0.0, step=0.01, format="%.4f")
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
                    st.info(f"Take Profit Price: **${take_profit_price:.4f}**")
                else:
                    take_profit_price = None
            else:
                take_profit_price = st.number_input("Take Profit Price", min_value=0.0, step=0.01, format="%.4f")
        else:
            take_profit_price = None
    
    # Display risk/reward
    if stop_loss_price and take_profit_price and order_price:
        risk_amount = abs(order_price - stop_loss_price) * order_amount
        reward_amount = abs(take_profit_price - order_price) * order_amount
        rr_ratio = reward_amount / risk_amount if risk_amount > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Risk Amount", f"${risk_amount:.4f}")
        col2.metric("Reward Amount", f"${reward_amount:.4f}")
        col3.metric("R:R Ratio", f"1:{rr_ratio:.2f}")
    
    st.divider()
    
    # ---------- ACTION BUTTONS ----------
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🚀 Place Order", type="primary", use_container_width=True):
            if not st.session_state.get("execution_mgr"):
                # Initialize execution manager
                from engine.execution_manager import ExecutionManager
                st.session_state.execution_mgr = ExecutionManager(
                    st.session_state["exchange"],
                    st.session_state.get("markets", {})
                )
            
            execution_mgr = st.session_state["execution_mgr"]
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
                
                # Place SL/TP if enabled
                if use_stop_loss and stop_loss_price:
                    sl_result = place_stop_loss_order(
                        st.session_state["exchange"],
                        st.session_state.get("markets", {}),
                        order_symbol,
                        order_side,
                        order_amount,
                        stop_loss_price
                    )
                    if sl_result["success"]:
                        st.success(f"✅ Stop loss placed at ${stop_loss_price:.4f}")
                    else:
                        st.warning(f"⚠️ Stop loss failed: {sl_result.get('error')}")
                
                if use_take_profit and take_profit_price:
                    tp_result = place_take_profit_order(
                        st.session_state["exchange"],
                        st.session_state.get("markets", {}),
                        order_symbol,
                        order_side,
                        order_amount,
                        take_profit_price
                    )
                    if tp_result["success"]:
                        st.success(f"✅ Take profit placed at ${take_profit_price:.4f}")
                    else:
                        st.warning(f"⚠️ Take profit failed: {tp_result.get('error')}")
                
                time.sleep(1)
                st.rerun()
            else:
                st.error(f"❌ Order failed: {result.get('error')}")
    
    with col2:
        if st.button("🧪 Validate Order", use_container_width=True):
            if order_price and order_price > 0 and order_amount and order_amount > 0:
                is_valid, result = validate_order_params(
                    st.session_state["exchange"],
                    st.session_state.get("markets", {}),
                    order_symbol,
                    order_side,
                    float(order_amount),
                    float(order_price)
                )
                if is_valid:
                    st.success(f"✅ Order valid - Amount: {result['amount']}, Price: {result['price']}")
                else:
                    st.error(f"❌ {result}")
            else:
                st.error("❌ Invalid amount or price")
    
    with col3:
        if st.button("🔄 Reset Form", use_container_width=True):
            st.rerun()
    
    st.divider()
    
    # ---------- OPEN ORDERS ----------
    st.subheader("📋 Open Orders")
    try:
        open_orders = st.session_state["exchange"].fetch_open_orders()
        if open_orders:
            df = pd.DataFrame(open_orders)
            st.dataframe(df[['symbol', 'side', 'type', 'amount', 'price', 'status']], use_container_width=True)
        else:
            st.info("No open orders")
    except Exception as e:
        st.error(f"Failed to fetch open orders: {str(e)}")
    
    st.divider()
    
    # ---------- ORDER HISTORY ----------
    st.subheader("📜 Order History")
    if "order_history" in st.session_state and st.session_state["order_history"]:
        df = pd.DataFrame(st.session_state["order_history"])
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No order history")

else:
    st.info("👈 Connect to Kraken to start trading")