"""Risk management with hard limits and kill-switch."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import logging

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

    # legacy aliases used elsewhere
    max_position_size: float | None = None
    daily_loss_limit_pct: float | None = None
    max_position_risk_pct: float | None = None
    max_drawdown: float | None = None
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


class RiskManager:
    """Professional risk controls for strategy execution."""

    def __init__(
        self,
        initial_balance: float | RiskConfig = 1000.0,
        max_position_pct: float | RiskConfig = 0.02,
        max_daily_loss_pct: float = 0.05,
        max_drawdown_pct: float = 0.15,
        max_concurrent_positions: int = 3,
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
        elif isinstance(max_position_pct, RiskConfig):
            cfg = max_position_pct
            max_position_pct = cfg.max_position_pct
            max_daily_loss_pct = cfg.max_daily_loss_pct
            max_drawdown_pct = cfg.max_drawdown_pct
            max_concurrent_positions = cfg.max_concurrent_positions

        self.account_balance = float(initial_balance)
        self.peak_balance = float(initial_balance)

        self.max_position_pct = float(max_position_pct)
        self.max_daily_loss_pct = float(max_daily_loss_pct)
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.max_concurrent_positions = int(max_concurrent_positions)

        self.positions: dict[str, dict[str, Any]] = {}
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self._daily_date = datetime.now(timezone.utc).date()

        self.kill_switch_active = False
        self.kill_switch_reason = ""

        logger.info(
            "RiskManager initialized | balance=%.2f max_pos=%.2f%% daily_loss=%.2f%% max_dd=%.2f%% max_positions=%d",
            self.account_balance,
            self.max_position_pct * 100,
            self.max_daily_loss_pct * 100,
            self.max_drawdown_pct * 100,
            self.max_concurrent_positions,
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
            logger.info("Daily PnL reset | prev_daily_pnl=%.2f", self.daily_pnl)
            self.daily_pnl = 0.0
            self._daily_date = today

    def _drawdown_pct(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return max(0.0, (self.peak_balance - self.account_balance) / self.peak_balance)

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

        if self.daily_pnl <= -(self.account_balance * self.max_daily_loss_pct):
            self._activate_kill_switch("daily loss limit exceeded")
            return False, "daily loss limit exceeded"

        if self._drawdown_pct() >= self.max_drawdown_pct:
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

    def open_position(self, symbol: str, side: str, quantity: float, price: float) -> None:
        self.positions[symbol] = {
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "entry_price": float(price),
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Position opened | %s %s qty=%s price=%s", symbol, side, quantity, price)

    def close_position(self, symbol: str, exit_price: float) -> float:
        pos = self.positions.get(symbol)
        if not pos:
            return 0.0

        qty = float(pos["quantity"])
        entry = float(pos["entry_price"])
        side = str(pos["side"]).lower()
        exit_price = float(exit_price)

        pnl = (exit_price - entry) * qty if side == "buy" else (entry - exit_price) * qty

        self.account_balance += pnl
        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.peak_balance = max(self.peak_balance, self.account_balance)

        del self.positions[symbol]
        logger.info("Position closed | %s pnl=%.2f balance=%.2f", symbol, pnl, self.account_balance)
        return pnl

    def get_risk_metrics(self) -> dict[str, Any]:
        self._reset_daily_if_needed()
        return {
            "account_balance": round(self.account_balance, 2),
            "total_pnl": round(self.total_pnl, 2),
            "daily_loss": round(min(0.0, self.daily_pnl), 2),
            "drawdown_pct": round(self._drawdown_pct() * 100, 2),
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
            "daily_loss": metrics.get("daily_loss", 0.0),
            "drawdown_pct": metrics.get("drawdown_pct", 0.0),
            "open_positions": metrics.get("open_positions", len(self.positions)),
            "kill_switch_active": metrics.get("kill_switch_active", False),
            "kill_switch_reason": metrics.get("kill_switch_reason", ""),
            # keep full payload too
            "metrics": metrics,
        }
