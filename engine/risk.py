"""Risk validation and sizing."""
from typing import Dict, Literal

OrderSide = Literal["buy", "sell"]


class RiskManager:
    """Manages risk calculations and validation."""
    
    def __init__(self, max_risk_per_trade: float = 2.0):
        self.max_risk_per_trade = max_risk_per_trade
    
    def calculate_stop_loss(
        self,
        entry: float,
        side: OrderSide,
        pct: float,
        buffer: float = 0.01
    ) -> Dict[str, float]:
        """Calculate stop loss prices."""
        if side == "buy":
            sl_raw = entry * (1 - pct / 100)
            sl_final = sl_raw * (1 - buffer)
            risk_per_unit = entry - sl_final
        else:
            sl_raw = entry * (1 + pct / 100)
            sl_final = sl_raw * (1 + buffer)
            risk_per_unit = sl_final - entry

        return {
            "sl_raw": sl_raw,
            "sl_final": sl_final,
            "risk_per_unit": risk_per_unit
        }
    
    def compute_risk_metrics(
        self,
        entry: float,
        amount: float,
        side: OrderSide,
        sl_pct: float,
        portfolio_value: float,
        buffer: float = 0.01
    ) -> Dict[str, float | bool | list[str]]:
        """Compute comprehensive risk metrics."""
        sl = self.calculate_stop_loss(entry, side, sl_pct, buffer)
        risk_total = sl["risk_per_unit"] * amount
        risk_pct_of_portfolio = (risk_total / portfolio_value * 100) if portfolio_value > 0 else 0

        risk_sane = True
        risk_debug: list[str] = []
        
        if portfolio_value <= 0:
            risk_sane = False
            risk_debug.append("Portfolio value is zero or not set.")
        if amount <= 0:
            risk_sane = False
            risk_debug.append("Trade amount is zero.")
        if risk_total <= 0:
            risk_sane = False
            risk_debug.append("Risk per trade is zero or negative.")
        if risk_pct_of_portfolio > self.max_risk_per_trade:
            risk_sane = False
            risk_debug.append(
                f"Risk per trade ({risk_pct_of_portfolio:.2f}%) exceeds allowed ({self.max_risk_per_trade:.2f}%)."
            )

        return {
            "sl_raw": sl["sl_raw"],
            "sl_final": sl["sl_final"],
            "risk_per_unit": sl["risk_per_unit"],
            "risk_total": risk_total,
            "risk_pct_of_portfolio": risk_pct_of_portfolio,
            "risk_sane": risk_sane,
            "risk_debug": risk_debug
        }
    
    def set_max_risk_per_trade(self, percentage: float) -> None:
        """Update maximum risk per trade."""
        self.max_risk_per_trade = percentage