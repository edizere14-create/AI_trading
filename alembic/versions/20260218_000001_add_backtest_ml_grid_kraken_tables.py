from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260218_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initial_capital", sa.Numeric(15, 2), nullable=False),
        sa.Column("final_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("total_return", sa.Numeric(10, 4), nullable=False),
        sa.Column("sharpe_ratio", sa.Numeric(10, 4)),
        sa.Column("sortino_ratio", sa.Numeric(10, 4)),
        sa.Column("max_drawdown", sa.Numeric(10, 4)),
        sa.Column("total_trades", sa.Integer),
        sa.Column("win_rate", sa.Numeric(10, 4)),
        sa.Column("profit_factor", sa.Numeric(10, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "strategy_name", "start_date", "end_date", name="uq_backtest_run"),
    )

    op.create_table(
        "ml_signals",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("signal_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lstm_prediction", sa.Numeric(10, 6)),
        sa.Column("rf_prediction", sa.Numeric(10, 6)),
        sa.Column("ensemble_signal", sa.Numeric(10, 6)),
        sa.Column("actual_return", sa.Numeric(10, 6)),
        sa.Column("signal_correct", sa.Boolean),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ml_signals_symbol_time", "ml_signals", ["symbol", "signal_timestamp"])

    op.create_table(
        "grid_trades",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("grid_level", sa.Integer),
        sa.Column("side", sa.String(10)),
        sa.Column("entry_price", sa.Numeric(15, 8)),
        sa.Column("exit_price", sa.Numeric(15, 8)),
        sa.Column("amount", sa.Numeric(15, 8)),
        sa.Column("pnl", sa.Numeric(15, 8)),
        sa.Column("pnl_percent", sa.Numeric(10, 4)),
        sa.Column("entry_time", sa.DateTime(timezone=True)),
        sa.Column("exit_time", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_grid_trades_symbol_status", "grid_trades", ["symbol", "status"])

    op.create_table(
        "kraken_orders",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("kraken_txid", sa.String(100), unique=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10)),
        sa.Column("order_type", sa.String(20)),
        sa.Column("price", sa.Numeric(15, 8)),
        sa.Column("volume", sa.Numeric(15, 8)),
        sa.Column("filled_volume", sa.Numeric(15, 8)),
        sa.Column("status", sa.String(20)),
        sa.Column("placed_at", sa.DateTime(timezone=True)),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kraken_orders_symbol_status", "kraken_orders", ["symbol", "status"])


def downgrade() -> None:
    op.drop_index("ix_kraken_orders_symbol_status", table_name="kraken_orders")
    op.drop_table("kraken_orders")

    op.drop_index("ix_grid_trades_symbol_status", table_name="grid_trades")
    op.drop_table("grid_trades")

    op.drop_index("ix_ml_signals_symbol_time", table_name="ml_signals")
    op.drop_table("ml_signals")

    op.drop_table("backtest_results")