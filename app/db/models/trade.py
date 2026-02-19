from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum, Boolean, Numeric
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Any
import enum

Base: Any = declarative_base()

class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol = Column(String, nullable=False)
    side: Any = Column(Enum(OrderSide), nullable=False)
    order_type = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    initial_capital = Column(Numeric(15, 2), nullable=False)
    final_value = Column(Numeric(15, 2), nullable=False)
    total_return = Column(Numeric(10, 4), nullable=False)
    sharpe_ratio = Column(Numeric(10, 4))
    sortino_ratio = Column(Numeric(10, 4))
    max_drawdown = Column(Numeric(10, 4))
    total_trades = Column(Integer)
    win_rate = Column(Numeric(10, 4))
    profit_factor = Column(Numeric(10, 4))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MLSignal(Base):
    __tablename__ = "ml_signals"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    signal_timestamp = Column(DateTime(timezone=True), nullable=False)
    lstm_prediction = Column(Numeric(10, 6))
    rf_prediction = Column(Numeric(10, 6))
    ensemble_signal = Column(Numeric(10, 6))
    actual_return = Column(Numeric(10, 6))
    signal_correct = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridTrade(Base):
    __tablename__ = "grid_trades"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    grid_level = Column(Integer)
    side = Column(String(10))
    entry_price = Column(Numeric(15, 8))
    exit_price = Column(Numeric(15, 8))
    amount = Column(Numeric(15, 8))
    pnl = Column(Numeric(15, 8))
    pnl_percent = Column(Numeric(10, 4))
    entry_time = Column(DateTime(timezone=True))
    exit_time = Column(DateTime(timezone=True))
    status = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KrakenOrder(Base):
    __tablename__ = "kraken_orders"

    id = Column(Integer, primary_key=True, index=True)
    kraken_txid = Column(String(100), unique=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10))
    order_type = Column(String(20))
    price = Column(Numeric(15, 8))
    volume = Column(Numeric(15, 8))
    filled_volume = Column(Numeric(15, 8))
    status = Column(String(20))
    placed_at = Column(DateTime(timezone=True))
    filled_at = Column(DateTime(timezone=True))
    canceled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())