import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import asyncio
from app.db.database import SessionLocal
from app.db.models import BacktestResult, GridTrade, KrakenOrder
import logging

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Trading Risk Dashboard", layout="wide")

# Theme
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
    .negative { color: #d32f2f; }
    .positive { color: #388e3c; }
</style>
""", unsafe_allow_html=True)


def get_backtest_metrics():
    """Fetch backtest metrics from DB"""
    db = SessionLocal()
    try:
        results = db.query(BacktestResult).order_by(BacktestResult.created_at.desc()).limit(10).all()
        return pd.DataFrame([
            {
                'symbol': r.symbol,
                'strategy': r.strategy_name,
                'total_return': float(r.total_return),
                'sharpe_ratio': float(r.sharpe_ratio) if r.sharpe_ratio else 0,
                'max_drawdown': float(r.max_drawdown) if r.max_drawdown else 0,
                'total_trades': r.total_trades,
                'win_rate': float(r.win_rate) if r.win_rate else 0,
                'final_value': float(r.final_value),
            }
            for r in results
        ])
    finally:
        db.close()


def get_grid_trades():
    """Fetch grid trades from DB"""
    db = SessionLocal()
    try:
        trades = db.query(GridTrade).order_by(GridTrade.created_at.desc()).limit(50).all()
        return pd.DataFrame([
            {
                'symbol': t.symbol,
                'side': t.side,
                'entry_price': float(t.entry_price) if t.entry_price else 0,
                'exit_price': float(t.exit_price) if t.exit_price else 0,
                'pnl': float(t.pnl) if t.pnl else 0,
                'pnl_percent': float(t.pnl_percent) if t.pnl_percent else 0,
                'status': t.status,
                'created_at': t.created_at,
            }
            for t in trades
        ])
    finally:
        db.close()


def get_kraken_orders():
    """Fetch Kraken orders from DB"""
    db = SessionLocal()
    try:
        orders = db.query(KrakenOrder).order_by(KrakenOrder.created_at.desc()).limit(50).all()
        return pd.DataFrame([
            {
                'symbol': o.symbol,
                'side': o.side,
                'price': float(o.price) if o.price else 0,
                'volume': float(o.volume) if o.volume else 0,
                'status': o.status,
                'created_at': o.created_at,
            }
            for o in orders
        ])
    finally:
        db.close()


# Header
st.title("🎯 AI Trading Risk Dashboard")
st.subheader("Real-time Portfolio & Strategy Analytics")

# KPIs
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Portfolio Value",
        value="$125,432",
        delta="$5,234 (+4.4%)",
        delta_color="normal"
    )

with col2:
    st.metric(
        label="24h P&L",
        value="$2,145",
        delta_color="normal"
    )

with col3:
    st.metric(
        label="Sharpe Ratio",
        value="2.34",
        delta="From 2.12"
    )

with col4:
    st.metric(
        label="Max Drawdown",
        value="-8.5%",
        delta="Improved"
    )

st.divider()

# Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Backtest Results",
    "📈 Grid Trades",
    "🔄 Kraken Orders",
    "⚠️ Risk Metrics",
    "🎲 Strategy Comparison"
])

# Tab 1: Backtest Results
with tab1:
    st.subheader("Latest Backtest Results")
    df_backtest = get_backtest_metrics()
    
    if not df_backtest.empty:
        st.dataframe(df_backtest, use_container_width=True)
        
        # Charts
        col1, col2 = st.columns(2)
        
        with col1:
            fig = px.bar(
                df_backtest,
                x='symbol',
                y='total_return',
                color='sharpe_ratio',
                title="Returns by Symbol"
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            fig = px.scatter(
                df_backtest,
                x='max_drawdown',
                y='sharpe_ratio',
                size='total_trades',
                color='symbol',
                title="Risk-Adjusted Returns"
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No backtest results yet")

# Tab 2: Grid Trades
with tab2:
    st.subheader("Grid Trading Performance")
    df_grid = get_grid_trades()
    
    if not df_grid.empty:
        st.dataframe(df_grid, use_container_width=True)
        
        # P&L distribution
        fig = px.histogram(
            df_grid,
            x='pnl',
            nbins=20,
            title="P&L Distribution",
            color_discrete_sequence=['#388e3c', '#d32f2f']
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # Win rate
        wins = len(df_grid[df_grid['pnl'] > 0])
        total = len(df_grid)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        st.metric("Grid Win Rate", f"{win_rate:.1f}%")
    else:
        st.info("No grid trades yet")

# Tab 3: Kraken Orders
with tab3:
    st.subheader("Kraken Orders")
    df_orders = get_kraken_orders()
    
    if not df_orders.empty:
        st.dataframe(df_orders, use_container_width=True)
        
        # Status distribution
        status_counts = df_orders['status'].value_counts()
        fig = px.pie(
            values=status_counts.values,
            names=status_counts.index,
            title="Order Status Distribution"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No Kraken orders yet")

# Tab 4: Risk Metrics
with tab4:
    st.subheader("Risk Analytics")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Value at Risk (95%)", "-$4,521")
    with col2:
        st.metric("Sortino Ratio", "3.45")
    with col3:
        st.metric("Calmar Ratio", "4.12")
    
    # Equity curve simulation
    st.line_chart({
        'Equity': [100, 102, 101, 105, 103, 108, 106, 110, 112, 115],
        'Benchmark': [100, 101, 100, 102, 101, 103, 102, 104, 105, 106]
    })

# Tab 5: Strategy Comparison
with tab5:
    st.subheader("Strategy Performance Comparison")
    
    comparison_data = pd.DataFrame({
        'Strategy': ['Grid Trading', 'DCA', 'ML Signals'],
        'Return %': [12.5, 8.3, 15.8],
        'Sharpe': [2.34, 1.89, 2.67],
        'Drawdown %': [-8.5, -5.2, -10.1],
        'Win Rate %': [55.3, 100, 62.4]
    })
    
    fig = go.Figure(data=[
        go.Scatterpolar(
            r=comparison_data['Return %'],
            theta=comparison_data['Strategy'],
            fill='toself',
            name='Return %'
        )
    ])
    fig.update_layout(title="Strategy Radar", polar=dict(radialaxis=dict(visible=True)))
    st.plotly_chart(fig, use_container_width=True)
    
    st.dataframe(comparison_data, use_container_width=True)

st.divider()
st.caption("Data updated every 60 seconds | Last update: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))