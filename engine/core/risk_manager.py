"""Risk management with hard limits and kill-switch."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import logging
import os

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Compatibility config for old/new parameter names."""
    # canonical
    initial_balance: float = 1000.0
    max_position_pct: float = 0.02
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_concurrent_positions: int = 3
    max_leverage_ratio: float = 5.0

    # legacy aliases used elsewhere
    max_position_size: float | None = None
    daily_loss_limit_pct: float | None = None
    max_position_risk_pct: float | None = None
    max_drawdown: float | None = None
    max_leverage: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    def __post_init__(self) -> None:
        # normalize legacy -> canonical
        if self.max_position_size is not None:
            self.max_position_pct = float(self.max_position_size)
        if self.max_position_risk_pct is not None:
            self.max_position_pct = float(self.max_position_risk_pct)
        if self.daily_loss_limit_pct is not None:
            self.max_daily_loss_pct = float(self.daily_loss_limit_pct)
        if self.max_drawdown is not None:
            self.max_drawdown_pct = float(self.max_drawdown)
        if self.max_leverage is not None:
            self.max_leverage_ratio = float(self.max_leverage)


class RiskManager:
    """Professional risk controls for strategy execution."""

    def __init__(
        self,
        initial_balance: float | RiskConfig = 1000.0,
        max_position_pct: float | RiskConfig = 0.02,
        max_daily_loss_pct: float = 0.05,
        max_drawdown_pct: float = 0.15,
        max_concurrent_positions: int = 3,
        daily_profit_target_pct: float = 0.10,
        max_leverage_ratio: float | None = None,
    ) -> None:
        # Accept RiskManager(RiskConfig(...))
        # Accept RiskManager(balance, RiskConfig(...))  <-- your current call
        if isinstance(initial_balance, RiskConfig):
            cfg = initial_balance
            initial_balance = cfg.initial_balance
            max_position_pct = cfg.max_position_pct
            max_daily_loss_pct = cfg.max_daily_loss_pct
            max_drawdown_pct = cfg.max_drawdown_pct
            max_concurrent_positions = cfg.max_concurrent_positions
            max_leverage_ratio = cfg.max_leverage_ratio
        elif isinstance(max_position_pct, RiskConfig):
            cfg = max_position_pct
            max_position_pct = cfg.max_position_pct
            max_daily_loss_pct = cfg.max_daily_loss_pct
            max_drawdown_pct = cfg.max_drawdown_pct
            max_concurrent_positions = cfg.max_concurrent_positions
            max_leverage_ratio = cfg.max_leverage_ratio

        self.account_balance = float(initial_balance)
        self.peak_balance = float(initial_balance)
        self.start_of_day_balance = float(initial_balance)

        self.max_position_pct = float(max_position_pct)
        self.max_daily_loss_pct = float(max_daily_loss_pct)
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.max_concurrent_positions = int(max_concurrent_positions)
        self.daily_profit_target = float(daily_profit_target_pct)
        self.daily_loss_limit = float(self.max_daily_loss_pct)
        if max_leverage_ratio is None:
            try:
                max_leverage_ratio = float(os.getenv("MAX_LEVERAGE_RATIO", "5.0") or "5.0")
            except (TypeError, ValueError):
                max_leverage_ratio = 5.0
        self.max_leverage_ratio = max(0.1, float(max_leverage_ratio))

        self.positions: dict[str, dict[str, Any]] = {}
        self.total_pnl = 0.0  # Backward-compatible alias for total realized PnL
        self.realized_pnl_total = 0.0
        self.daily_pnl = 0.0  # Backward-compatible alias for daily realized PnL
        self.daily_realized_pnl = 0.0
        self.daily_gross_profit = 0.0
        self.daily_gross_loss = 0.0  # positive absolute value of losses
        self.daily_trade_count = 0
        self.daily_win_count = 0
        self._daily_date = datetime.now(timezone.utc).date()

        self.kill_switch_active = False
        self.kill_switch_reason = ""

        logger.info(
            "RiskManager initialized | balance=%.2f max_pos=%.2f%% daily_loss=%.2f%% max_dd=%.2f%% max_positions=%d max_lev=%.2fx",
            self.account_balance,
            self.max_position_pct * 100,
            self.max_daily_loss_pct * 100,
            self.max_drawdown_pct * 100,
            self.max_concurrent_positions,
            self.max_leverage_ratio,
        )

    # Backward-compat alias
    @property
    def current_balance(self) -> float:
        return self.account_balance

    @current_balance.setter
    def current_balance(self, value: float) -> None:
        self.account_balance = float(value)

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._daily_date:
            logger.info(
                "Daily reset | prev_daily_realized_pnl=%.2f trades=%d wins=%d",
                self.daily_realized_pnl,
                self.daily_trade_count,
                self.daily_win_count,
            )
            self.start_of_day_balance = float(self.account_balance)
            self.daily_realized_pnl = 0.0
            self.daily_pnl = 0.0
            self.daily_gross_profit = 0.0
            self.daily_gross_loss = 0.0
            self.daily_trade_count = 0
            self.daily_win_count = 0
            self._daily_date = today

    def _drawdown_pct(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return max(0.0, (self.peak_balance - self.account_balance) / self.peak_balance)

    def calculate_daily_pnl(self) -> tuple[float, float]:
        """
        Calculates realized daily PnL from start of UTC day.
        Returns: (pnl_value, pnl_percent)
        """
        self._reset_daily_if_needed()
        pnl = float(self.account_balance - self.start_of_day_balance)
        if self.start_of_day_balance <= 0:
            return pnl, 0.0
        pnl_pct = (pnl / self.start_of_day_balance) * 100.0
        return pnl, pnl_pct

    def can_trade(self) -> tuple[bool, str]:
        """Enforces daily risk thresholds against realized PnL."""
        self._reset_daily_if_needed()
        _, pnl_percent = self.calculate_daily_pnl()

        loss_limit_pct = self.daily_loss_limit * 100.0
        if pnl_percent <= -loss_limit_pct:
            self._activate_kill_switch("daily loss limit exceeded")
            return False, "daily loss limit exceeded"

        profit_target_pct = self.daily_profit_target * 100.0
        if profit_target_pct > 0 and pnl_percent >= profit_target_pct:
            return False, "daily profit target reached"

        return True, "ok"

    def _activate_kill_switch(self, reason: str) -> None:
        if not self.kill_switch_active:
            self.kill_switch_active = True
            self.kill_switch_reason = reason
            logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.kill_switch_reason = ""
        logger.warning("Kill switch reset manually")

    def can_open_position(self, symbol: str, quantity: float, price: float) -> tuple[bool, str]:
        self._reset_daily_if_needed()

        if self.kill_switch_active:
            return False, f"kill-switch active: {self.kill_switch_reason}"

        can_trade_now, reason = self.can_trade()
        if not can_trade_now:
            return False, reason

        # Check both realized and mark-to-market drawdown
        if self._drawdown_pct() >= self.max_drawdown_pct or self._equity_drawdown_pct() >= self.max_drawdown_pct:
            self._activate_kill_switch("max drawdown exceeded")
            return False, "max drawdown exceeded"

        if len(self.positions) >= self.max_concurrent_positions:
            return False, "max concurrent positions reached"

        if symbol in self.positions:
            return False, f"position already open for {symbol}"

        notional = float(quantity) * float(price)
        max_notional = self.account_balance * self.max_position_pct
        if notional > max_notional:
            return False, f"position too large ({notional:.2f} > {max_notional:.2f})"

        return True, "ok"

    def check_risk_limits(self, signal: dict[str, Any]) -> tuple[bool, str]:
        """Main validation entry used by execution flow."""
        symbol = str(signal.get("symbol", ""))
        quantity = float(signal.get("quantity", 0.0))
        price = float(signal.get("price", 0.0))

        if not symbol or quantity <= 0 or price <= 0:
            return False, "invalid signal fields"

        return self.can_open_position(symbol, quantity, price)

    def pre_trade_notional_check(
        self,
        contracts: int,
        contract_size: float,
        mark_price: float,
        symbol: str,
        inverse: bool = False,
    ) -> bool:
        """Secondary contract-level leverage check before order hits exchange."""
        qty_contracts = abs(float(contracts))
        size = float(contract_size)
        px = float(mark_price)
        if qty_contracts <= 0 or size <= 0 or px <= 0:
            logger.error(
                "[RISK MGR BLOCK] Invalid pre-trade sizing inputs | symbol=%s contracts=%s contract_size=%s mark=%s",
                symbol,
                contracts,
                contract_size,
                mark_price,
            )
            return False

        notional = (qty_contracts * size) if inverse else (qty_contracts * size * px)
        equity = float(self.account_balance)
        leverage = (notional / equity) if equity > 0 else float("inf")

        if leverage > self.max_leverage_ratio:
            logger.error(
                "[RISK MGR BLOCK] Pre-trade notional check failed: symbol=%s notional=%.2f equity=%.2f leverage=%.2fx max=%.2fx",
                symbol,
                notional,
                equity,
                leverage,
                self.max_leverage_ratio,
            )
            return False
        return True

    def open_position(self, symbol: str, side: str, quantity: float, price: float) -> None:
        self.positions[symbol] = {
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "entry_price": float(price),
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Position opened | %s %s qty=%s price=%s", symbol, side, quantity, price)

    def close_position(self, symbol: str, exit_price: float, fees: float = 0.0) -> float:
        pos = self.positions.get(symbol)
        if not pos:
            return 0.0

        qty = float(pos["quantity"])
        entry = float(pos["entry_price"])
        side = str(pos["side"]).lower()
        exit_price = float(exit_price)
        fees = abs(float(fees or 0.0))

        gross_pnl = (exit_price - entry) * qty if side == "buy" else (entry - exit_price) * qty
        net_pnl = gross_pnl - fees

        self.account_balance += net_pnl
        self.realized_pnl_total += net_pnl
        self.total_pnl = self.realized_pnl_total  # keep legacy alias in sync
        self.daily_realized_pnl += net_pnl
        self.daily_pnl = self.daily_realized_pnl  # keep legacy alias in sync
        self.daily_trade_count += 1
        if net_pnl > 0:
            self.daily_win_count += 1
            self.daily_gross_profit += net_pnl
        elif net_pnl < 0:
            self.daily_gross_loss += abs(net_pnl)
        self.peak_balance = max(self.peak_balance, self.account_balance)

        del self.positions[symbol]
        logger.info(
            "Position closed | %s gross_pnl=%.4f fees=%.4f net_pnl=%.4f balance=%.2f",
            symbol, gross_pnl, fees, net_pnl, self.account_balance,
        )
        return net_pnl

    # ------------------------------------------------------------------
    # Unrealized PnL & mark-to-market
    # ------------------------------------------------------------------

    def update_mark_price(self, symbol: str, mark_price: float) -> None:
        """Update the live mark price for an open position."""
        pos = self.positions.get(symbol)
        if pos:
            pos["mark_price"] = float(mark_price)

    def get_position_unrealized_pnl(self, symbol: str) -> float:
        """Compute unrealized PnL for a single open position."""
        pos = self.positions.get(symbol)
        if not pos:
            return 0.0
        qty = float(pos.get("quantity", 0.0))
        entry = float(pos.get("entry_price", 0.0))
        mark = float(pos.get("mark_price", entry))
        side = str(pos.get("side", "buy")).lower()
        if side == "buy":
            return (mark - entry) * qty
        return (entry - mark) * qty

    def get_total_unrealized_pnl(self) -> float:
        """Sum unrealized PnL across all open positions."""
        return sum(self.get_position_unrealized_pnl(s) for s in self.positions)

    def get_equity(self) -> float:
        """Account balance + total unrealized PnL (mark-to-market equity)."""
        return self.account_balance + self.get_total_unrealized_pnl()

    def _equity_drawdown_pct(self) -> float:
        """Drawdown based on mark-to-market equity (includes unrealized)."""
        equity = self.get_equity()
        if self.peak_balance <= 0:
            return 0.0
        return max(0.0, (self.peak_balance - equity) / self.peak_balance)

    def get_risk_metrics(self) -> dict[str, Any]:
        self._reset_daily_if_needed()
        daily_pnl_value, daily_pnl_percent = self.calculate_daily_pnl()
        win_rate = (
            (self.daily_win_count / self.daily_trade_count) * 100.0
            if self.daily_trade_count > 0
            else 0.0
        )
        profit_factor = (
            (self.daily_gross_profit / self.daily_gross_loss)
            if self.daily_gross_loss > 0
            else (None if self.daily_gross_profit > 0 else 0.0)
        )
        normalized_daily_pnl_per_1k = (
            (daily_pnl_value / self.start_of_day_balance) * 1000.0
            if self.start_of_day_balance > 0
            else 0.0
        )

        unrealized = self.get_total_unrealized_pnl()
        equity = self.get_equity()
        equity_dd = self._equity_drawdown_pct()

        return {
            "account_balance": round(self.account_balance, 2),
            "current_balance": round(self.account_balance, 2),
            "equity": round(equity, 2),
            "start_of_day_balance": round(self.start_of_day_balance, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_realized_pnl": round(self.realized_pnl_total, 2),
            "unrealized_pnl": round(unrealized, 4),
            "daily_pnl": round(self.daily_realized_pnl, 2),
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "daily_pnl_percent": round(daily_pnl_percent, 4),
            "daily_loss": round(min(0.0, self.daily_realized_pnl), 2),
            "daily_profit": round(max(0.0, self.daily_realized_pnl), 2),
            "daily_trade_count": int(self.daily_trade_count),
            "daily_win_count": int(self.daily_win_count),
            "win_rate": round(win_rate, 2),
            "profit_factor": (round(profit_factor, 4) if isinstance(profit_factor, (int, float)) else None),
            "daily_gross_profit": round(self.daily_gross_profit, 2),
            "daily_gross_loss": round(self.daily_gross_loss, 2),
            "daily_loss_limit_pct": round(self.daily_loss_limit * 100.0, 2),
            "daily_profit_target_pct": round(self.daily_profit_target * 100.0, 2),
            "max_leverage_ratio": round(self.max_leverage_ratio, 4),
            "normalized_daily_pnl_per_1k": round(normalized_daily_pnl_per_1k, 4),
            "drawdown_pct": round(self._drawdown_pct() * 100, 2),
            "equity_drawdown_pct": round(equity_dd * 100, 2),
            "open_positions": len(self.positions),
            "max_concurrent_positions": self.max_concurrent_positions,
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
        }

    def get_status(self) -> dict[str, Any]:
        """Backward-compatible status API expected by worker/routes."""
        metrics = self.get_risk_metrics()
        return {
            "account_balance": metrics.get("account_balance", self.account_balance),
            "equity": metrics.get("equity", self.account_balance),
            "unrealized_pnl": metrics.get("unrealized_pnl", 0.0),
            "daily_loss": metrics.get("daily_loss", 0.0),
            "daily_pnl": metrics.get("daily_pnl", 0.0),
            "daily_realized_pnl": metrics.get("daily_realized_pnl", 0.0),
            "daily_pnl_percent": metrics.get("daily_pnl_percent", 0.0),
            "win_rate": metrics.get("win_rate", 0.0),
            "profit_factor": metrics.get("profit_factor"),
            "drawdown_pct": metrics.get("drawdown_pct", 0.0),
            "equity_drawdown_pct": metrics.get("equity_drawdown_pct", 0.0),
            "open_positions": metrics.get("open_positions", len(self.positions)),
            "kill_switch_active": metrics.get("kill_switch_active", False),
            "kill_switch_reason": metrics.get("kill_switch_reason", ""),
            # keep full payload too
            "metrics": metrics,
        }
