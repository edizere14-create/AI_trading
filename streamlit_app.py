import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime
import time
from typing import Optional, Tuple, Dict, Any

st.set_page_config(
    page_title="AI Trading Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 AI Trading Dashboard — Kraken Live")

# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("🔐 Exchange Settings")

    # Choose between Spot and Futures
    kraken_env = st.selectbox(
        "Kraken Environment",
        ["Spot", "Futures (Demo)"]
    )

    api_key = st.text_input(
        "Kraken API Key",
        type="password",
        help="API key with trading enabled (NO withdrawals)"
    )

    api_secret = st.text_input(
        "Kraken API Secret",
        type="password"
    )

    connect = st.button("🔌 Connect to Kraken")

    if "exchange" in st.session_state:
        if st.button("🔌 Disconnect"):
            for key in ["exchange", "balance", "server_time", "markets"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

# ---------- CONNECTION ----------

# Add support for Spot and Futures (Demo)
@st.cache_resource
def get_kraken_exchange(api_key: str, api_secret: str, env: str):
    """Create and return a Kraken Spot or Futures (Demo) exchange instance"""
    if env == "Futures (Demo)":
        return ccxt.krakenfutures({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
            "test": True,  # Use demo environment
        })
    else:
        return ccxt.kraken({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        })


def connect_kraken(api_key: str, api_secret: str, env: str) -> tuple:
    try:
        exchange = get_kraken_exchange(api_key, api_secret, env)
        exchange.check_required_credentials()

        # Load markets for precision/limits
        markets = exchange.load_markets()

        # Test connection
        balance = exchange.fetch_balance()
        server_time = exchange.fetch_time()

        return exchange, balance, server_time, markets, None

    except ccxt.AuthenticationError as e:
        return None, None, None, None, f"Authentication failed: {str(e)}"
    except ccxt.NetworkError as e:
        return None, None, None, None, f"Network error: {str(e)}"
    except Exception as e:
        return None, None, None, None, f"Error: {str(e)}"


if connect:
    if not api_key or not api_secret:
        st.error("❌ Please enter both API key and secret")
    else:
        with st.spinner(f"Connecting to Kraken {kraken_env}..."):
            exchange, balance, server_time, markets, error = connect_kraken(api_key, api_secret, kraken_env)

        if exchange:
            st.session_state["exchange"] = exchange
            st.session_state["balance"] = balance
            st.session_state["server_time"] = server_time
            st.session_state["markets"] = markets
            st.session_state["connected_at"] = datetime.now()
            st.success(f"✅ Connected to Kraken {kraken_env} successfully")
            st.rerun()
        else:
            st.error(f"❌ Connection failed: {error}")

# ---------- ORDER EXECUTION FUNCTIONS ----------
def validate_order_params(exchange: ccxt.Exchange, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, Any]:
    """Validate order parameters against exchange limits"""
    try:
        market = st.session_state["markets"][symbol]
        
        # Get limits
        min_amount = market['limits']['amount']['min']
        max_amount = market['limits']['amount']['max']
        min_cost = market['limits']['cost']['min']
        
        # Amount precision
        amount_precision = market['precision']['amount']
        amount = exchange.amount_to_precision(symbol, amount)
        amount = float(amount)
        
        # Validate amount
        if amount < min_amount:
            return False, f"Amount too small. Minimum: {min_amount}"
        if max_amount and amount > max_amount:
            return False, f"Amount too large. Maximum: {max_amount}"
        
        # Validate cost (notional value)
        validated_price: Optional[float] = None
        if price is not None:
            price_precision = market['precision']['price']
            validated_price = float(exchange.price_to_precision(symbol, price))
            
            cost = amount * validated_price
            if cost < min_cost:
                return False, f"Order value too small. Minimum: ${min_cost}"
        
        return True, {"amount": amount, "price": validated_price}
        
    except Exception as e:
        return False, f"Validation error: {str(e)}"


def execute_order(exchange: ccxt.Exchange, symbol: str, side: str, order_type: str, amount: float, price: Optional[float] = None) -> Dict[str, Any]:
    """Execute order with comprehensive error handling and logging"""
    try:
        # Validate parameters
        is_valid, result = validate_order_params(exchange, symbol, side, amount, price)
        if not is_valid:
            return {"success": False, "error": result}
        
        validated_params = result
        amount = validated_params["amount"]
        price = validated_params["price"]
        
        # Place order
        if order_type == "market":
            order = exchange.create_market_order(symbol, side, amount)
        elif order_type == "limit":
            if not price:
                return {"success": False, "error": "Limit orders require a price"}
            order = exchange.create_limit_order(symbol, side, amount, price)
        else:
            return {"success": False, "error": f"Unsupported order type: {order_type}"}
        
        # Log order details
        order_log = {
            "success": True,
            "order_id": order.get("id"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "amount": order.get("amount"),
            "price": order.get("price"),
            "cost": order.get("cost"),
            "status": order.get("status"),
            "timestamp": order.get("timestamp"),
            "datetime": order.get("datetime"),
            "fees": order.get("fees"),
            "raw_response": order
        }
        
        return order_log
        
    except ccxt.InsufficientFunds as e:
        return {"success": False, "error": f"Insufficient funds: {str(e)}"}
    except ccxt.InvalidOrder as e:
        return {"success": False, "error": f"Invalid order: {str(e)}"}
    except ccxt.OrderNotFound as e:
        return {"success": False, "error": f"Order not found: {str(e)}"}
    except ccxt.NetworkError as e:
        return {"success": False, "error": f"Network error: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


def place_stop_loss_order(exchange: ccxt.Exchange, symbol: str, side: str, amount: float, stop_price: float, limit_price: Optional[float] = None) -> Dict[str, Any]:
    """Place a stop-loss order (stop-limit on Kraken)"""
    try:
        # Kraken uses stop-limit orders for stop losses
        # side: 'sell' for long positions, 'buy' for short positions
        opposite_side = 'sell' if side == 'buy' else 'buy'
        
        # If no limit price provided, use stop price
        if not limit_price:
            limit_price = stop_price
        
        # Validate parameters
        is_valid, result = validate_order_params(exchange, symbol, opposite_side, amount, limit_price)
        if not is_valid:
            return {"success": False, "error": result}
        
        validated_params = result
        amount = validated_params["amount"]
        limit_price = validated_params["price"]
        
        # Place stop-loss order
        order = exchange.create_order(
            symbol=symbol,
            type='stop-loss-limit',
            side=opposite_side,
            amount=amount,
            price=limit_price,
            params={'stopPrice': stop_price}
        )
        
        return {
            "success": True,
            "order_id": order.get("id"),
            "type": "stop-loss",
            "stop_price": stop_price,
            "limit_price": limit_price,
            "amount": amount,
            "raw_response": order
        }
        
    except Exception as e:
        return {"success": False, "error": f"Stop-loss order failed: {str(e)}"}


def place_take_profit_order(exchange: ccxt.Exchange, symbol: str, side: str, amount: float, take_profit_price: float) -> Dict[str, Any]:
    """Place a take-profit order (limit order)"""
    try:
        # Take profit is opposite side of entry
        opposite_side = 'sell' if side == 'buy' else 'buy'
        
        # Validate parameters
        is_valid, result = validate_order_params(exchange, symbol, opposite_side, amount, take_profit_price)
        if not is_valid:
            return {"success": False, "error": result}
        
        validated_params = result
        amount = validated_params["amount"]
        take_profit_price = validated_params["price"]
        
        # Place limit order at take profit price
        order = exchange.create_limit_order(symbol, opposite_side, amount, take_profit_price)
        
        return {
            "success": True,
            "order_id": order.get("id"),
            "type": "take-profit",
            "price": take_profit_price,
            "amount": amount,
            "raw_response": order
        }
        
    except Exception as e:
        return {"success": False, "error": f"Take-profit order failed: {str(e)}"}


# ---------- DASHBOARD ----------
if "exchange" in st.session_state:
    # Connection status
    connected_duration = datetime.now() - st.session_state.get("connected_at", datetime.now())
    st.success(f"🟢 Connected (Session: {connected_duration.seconds // 60}m {connected_duration.seconds % 60}s)")
    
    # Refresh button
    if st.button("🔄 Refresh Data"):
        try:
            st.session_state["balance"] = st.session_state["exchange"].fetch_balance()
            st.session_state["server_time"] = st.session_state["exchange"].fetch_time()
            st.rerun()
        except Exception as e:
            st.error(f"Failed to refresh: {str(e)}")
    
    st.subheader("📊 Account Overview")

    # Server time
    server_dt = datetime.fromtimestamp(
        st.session_state["server_time"] / 1000
    )
    local_dt = datetime.now()
    st.write(f"🕒 Kraken Server Time: **{server_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC**")
    st.write(f"🕒 Local Time: **{local_dt.strftime('%Y-%m-%d %H:%M:%S')}**")

    # Balances
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
        
        # Total portfolio value estimate (if USDT exists)
        if "USDT" in balances and balances["USDT"] > 0:
            st.metric("💰 USDT Balance", f"${balances['USDT']:,.2f}")
    else:
        st.info("No balances found")

    st.divider()

    # Market data section
    st.subheader("📈 Live Market Data")
    
    # Trading pair selector
    col1, col2 = st.columns([3, 1])
    with col1:
        trading_pair = st.selectbox(
            "Select Trading Pair",
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"],
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
        
        # Calculate 24h change
        change_pct = ticker.get('percentage')
        if change_pct is not None:
            col4.metric("24h Change", f"{change_pct:.2f}%", delta=f"{change_pct:.2f}%")
        
        # Additional info
        base_asset = trading_pair.split('/')[0] if trading_pair else "?"
        st.write(f"**Volume (24h):** {ticker.get('baseVolume', 0):,.2f} {base_asset}")
        st.write(f"**Bid:** ${ticker.get('bid', 0):,.2f} | **Ask:** ${ticker.get('ask', 0):,.2f}")
        
        # Auto-refresh logic
        if auto_refresh:
            time.sleep(5)
            st.rerun()
            
    except Exception as e:
        st.error(f"Failed to fetch market data: {str(e)}")
        current_price = 0

    st.divider()
    
    # ---------- ORDER PLACEMENT SECTION WITH STOP LOSS & TAKE PROFIT ----------
    st.subheader("⚡ Place Order with Stop Loss & Take Profit")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        order_symbol = st.selectbox("Symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"], key="order_symbol")
    
    with col2:
        order_side = st.selectbox("Side", ["buy", "sell"])
    
    with col3:
        order_type = st.selectbox("Type", ["market", "limit"])
    
    with col4:
        order_amount = st.number_input("Amount", min_value=0.0, step=0.001, format="%.6f")
    
    order_price = None
    if order_type == "limit":
        order_price = st.number_input("Price", min_value=0.0, value=float(current_price) if current_price > 0 else 0.0, step=0.01, format="%.2f")
    else:
        order_price = current_price
    
    # Display market info
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
            if not order_symbol:
                st.error("❌ Please select a symbol")
            elif order_amount <= 0:
                st.error("❌ Amount must be greater than 0")
            elif order_type == "limit" and (not order_price or order_price <= 0):
                st.error("❌ Limit orders require a valid price")
            else:
                with st.spinner("Placing order..."):
                    # Place main order
                    result = execute_order(
                        st.session_state["exchange"],
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
                    
                    # Store order
                    if "order_history" not in st.session_state:
                        st.session_state["order_history"] = []
                    st.session_state["order_history"].append(result)
                    
                    # Place stop loss if enabled
                    if use_stop_loss and stop_loss_price:
                        with st.spinner("Placing stop loss..."):
                            sl_result = place_stop_loss_order(
                                st.session_state["exchange"],
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
                    
                    # Place take profit if enabled
                    if use_take_profit and take_profit_price:
                        with st.spinner("Placing take profit..."):
                            tp_result = place_take_profit_order(
                                st.session_state["exchange"],
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
                    
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"❌ Order failed: {result['error']}")
    
    with col2:
        if st.button("🧪 Validate Order", use_container_width=True):
            is_valid, result = validate_order_params(
                st.session_state["exchange"],
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
            st.dataframe(df_orders[["symbol", "side", "type", "price", "amount", "status", "datetime"]], 
                        use_container_width=True)
            
            # Cancel order functionality
            if st.checkbox("Show Cancel Options"):
                order_id = st.text_input("Order ID to cancel")
                order_symbol_cancel = st.selectbox("Order Symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], key="cancel_symbol")
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
        display_cols = [col for col in ["order_id", "symbol", "side", "type", "amount", "price", "stop_price", "limit_price", "status", "datetime"] if col in df_history.columns]
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

# ---------- STOP-LOSS CALCULATOR & RISK SANITY CHECK (DROP-IN) ----------
st.subheader("🧮 Stop-Loss Calculator & Risk Sanity Check")

# User inputs
col1, col2, col3, col4 = st.columns(4)
with col1:
    sl_entry = st.number_input("Entry Price", min_value=0.0, value=float(order_price) if 'order_price' in locals() and order_price else 0.0, step=0.01, format="%.2f", key="sl_entry")
with col2:
    sl_amount = st.number_input("Trade Amount", min_value=0.0, value=float(order_amount) if 'order_amount' in locals() and order_amount else 0.0, step=0.0001, format="%.6f", key="sl_amount")
with col3:
    sl_side = st.selectbox("Side", ["buy", "sell"], index=0 if 'order_side' not in locals() else (0 if order_side == "buy" else 1), key="sl_side")
with col4:
    sl_pct = st.number_input("Stop Loss %", min_value=0.01, max_value=50.0, value=2.0, step=0.01, format="%.2f", key="sl_pct")

# Buffer (UI: percent, internal: fraction)
buffer_percent = st.number_input(
    "Buffer (%)",
    min_value=0.0,
    max_value=100.0,
    value=st.session_state.get("stop_buffer", 0.1) * 100,
    step=0.01,
    format="%.2f",
    key="sl_buffer"
)
sl_buffer = buffer_percent / 100  # Internal use as fraction

# Portfolio/risk
portfolio_value = st.session_state.get("portfolio_value", 0)
risk_per_trade = st.session_state.get("risk_per_trade", 1.0)

# Calculate stop price
if sl_side == "buy":
    sl_raw = sl_entry * (1 - sl_pct / 100)
    sl_final = sl_raw * (1 - sl_buffer)
    risk_per_unit = sl_entry - sl_final
else:
    sl_raw = sl_entry * (1 + sl_pct / 100)
    sl_final = sl_raw * (1 + sl_buffer)
    risk_per_unit = sl_final - sl_entry

risk_total = risk_per_unit * sl_amount
risk_pct_of_portfolio = (risk_total / portfolio_value * 100) if portfolio_value > 0 else 0

# RISK SANITY ENFORCEMENT (MANDATORY)
risk_sane = True
risk_debug = []
if portfolio_value <= 0:
    risk_sane = False
    risk_debug.append("Portfolio value is zero or not set.")
if sl_amount <= 0:
    risk_sane = False
    risk_debug.append("Trade amount is zero.")
if risk_total <= 0:
    risk_sane = False
    risk_debug.append("Risk per trade is zero or negative.")
if risk_pct_of_portfolio > risk_per_trade:
    risk_sane = False
    risk_debug.append(f"Risk per trade ({risk_pct_of_portfolio:.2f}%) exceeds allowed ({risk_per_trade:.2f}%).")

# UI Output
st.markdown(f"""
- **Entry:** ${sl_entry:,.2f}
- **Stop Loss (raw):** ${sl_raw:,.2f}
- **Stop Loss (buffered):** ${sl_final:,.2f}
- **Risk per unit:** ${risk_per_unit:,.4f}
- **Total risk:** ${risk_total:,.2f}
- **Portfolio value:** ${portfolio_value:,.2f}
- **Risk % of portfolio:** {risk_pct_of_portfolio:.2f}%
- **Allowed risk per trade:** {risk_per_trade:.2f}%
""")

if risk_sane:
    st.success("✅ Risk sanity check PASSED. You may proceed.")
else:
    st.error("❌ Risk sanity check FAILED. Please review your inputs.")
    st.warning(" | ".join(risk_debug))

# Debug transparency
with st.expander("🔎 Debug Details"):
    st.json({
        "entry": sl_entry,
        "amount": sl_amount,
        "side": sl_side,
        "stop_loss_pct": sl_pct,
        "buffer": sl_buffer,
        "stop_loss_raw": sl_raw,
        "stop_loss_final": sl_final,
        "risk_per_unit": risk_per_unit,
        "risk_total": risk_total,
        "portfolio_value": portfolio_value,
        "risk_pct_of_portfolio": risk_pct_of_portfolio,
        "risk_per_trade_allowed": risk_per_trade,
        "risk_sane": risk_sane,
        "risk_debug": risk_debug
    })

# ENFORCE: Block order preview/execution if not sane
if not risk_sane:
    st.stop()
# ---------- END STOP-LOSS CALCULATOR & RISK SANITY CHECK ----------

# ---------- ORDER EXECUTION BUTTON (REAL ORDER PLACEMENT) ----------
if st.button("🚀 Place Order (Risk-Checked)", type="primary"):
    with st.spinner("Placing order..."):
        # Use the calculated stop-loss price (buffered)
        # Use sl_final as the stop-loss price for stop-loss order
        # Use sl_entry as entry price, sl_amount as amount, sl_side as side

        # Main order
        result = execute_order(
            st.session_state["exchange"],
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

        # Store order
        if "order_history" not in st.session_state:
            st.session_state["order_history"] = []
        st.session_state["order_history"].append(result)

        # Place stop loss using sl_final (buffered stop)
        if use_stop_loss and sl_final:
            with st.spinner("Placing stop loss..."):
                sl_result = place_stop_loss_order(
                    st.session_state["exchange"],
                    order_symbol,
                    order_side,
                    order_amount,
                    sl_final  # Use buffered stop-loss
                )

            if sl_result["success"]:
                st.success(f"✅ Stop loss placed at ${sl_final:,.2f}")
                st.session_state["order_history"].append(sl_result)
            else:
                st.warning(f"⚠️ Stop loss failed: {sl_result['error']}")

        # Place take profit if enabled
        if use_take_profit and take_profit_price:
            with st.spinner("Placing take profit..."):
                tp_result = place_take_profit_order(
                    st.session_state["exchange"],
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

        time.sleep(1)
        st.rerun()
    else:
        st.error(f"❌ Order failed: {result['error']}")
# ---------- END ORDER EXECUTION BUTTON ----------