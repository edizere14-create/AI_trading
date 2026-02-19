CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy_name VARCHAR(100) NOT NULL,
    start_date TIMESTAMP NOT NULL,
    end_date TIMESTAMP NOT NULL,
    initial_capital DECIMAL(15,2) NOT NULL,
    final_value DECIMAL(15,2) NOT NULL,
    total_return DECIMAL(10,4) NOT NULL,
    sharpe_ratio DECIMAL(10,4),
    sortino_ratio DECIMAL(10,4),
    max_drawdown DECIMAL(10,4),
    total_trades INTEGER,
    win_rate DECIMAL(10,4),
    profit_factor DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_backtest UNIQUE(symbol, strategy_name, start_date, end_date)
);

CREATE TABLE IF NOT EXISTS ml_signals (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    signal_timestamp TIMESTAMP NOT NULL,
    lstm_prediction DECIMAL(10,6),
    rf_prediction DECIMAL(10,6),
    ensemble_signal DECIMAL(10,6),
    actual_return DECIMAL(10,6),
    signal_correct BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_time (symbol, signal_timestamp)
);

CREATE TABLE IF NOT EXISTS grid_trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    grid_level INTEGER,
    side VARCHAR(10),
    entry_price DECIMAL(15,8),
    exit_price DECIMAL(15,8),
    amount DECIMAL(15,8),
    pnl DECIMAL(15,8),
    pnl_percent DECIMAL(10,4),
    entry_time TIMESTAMP,
    exit_time TIMESTAMP,
    status VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_status (symbol, status)
);

CREATE TABLE IF NOT EXISTS kraken_orders (
    id SERIAL PRIMARY KEY,
    kraken_txid VARCHAR(100) UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10),
    order_type VARCHAR(20),
    price DECIMAL(15,8),
    volume DECIMAL(15,8),
    filled_volume DECIMAL(15,8),
    status VARCHAR(20),
    placed_at TIMESTAMP,
    filled_at TIMESTAMP,
    canceled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_status (symbol, status)
);