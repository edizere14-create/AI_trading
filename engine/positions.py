"""Position state management."""
from typing import Any, Dict, Optional, List
import ccxt  # type: ignore


class PositionManager:
    """Manages position tracking and validation."""
    
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange
        self.positions_cache: Dict[str, Dict[str, Any]] = {}
    
    def fetch_positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Fetch open positions from exchange."""
        try:
            if hasattr(self.exchange, 'fetch_positions'):
                positions = self.exchange.fetch_positions(symbols=[symbol] if symbol else None)
                
                if symbol:
                    self.positions_cache[symbol] = positions[0] if positions else {}
                else:
                    for pos in positions:
                        sym = pos.get("symbol")
                        if sym:
                            self.positions_cache[sym] = pos
                
                return {
                    "success": True,
                    "positions": positions,
                    "count": len(positions)
                }
            else:
                return {
                    "success": False,
                    "error": "Exchange does not support fetch_positions()",
                    "positions": [],
                    "count": 0
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch positions: {str(e)}", "positions": [], "count": 0}
    
    def get_position_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """Get position details for a specific symbol."""
        try:
            result = self.fetch_positions(symbol)
            if not result["success"]:
                return result
            
            positions = result["positions"]
            if not positions:
                return {
                    "success": True,
                    "has_position": False,
                    "position": None,
                    "message": f"No open position for {symbol}"
                }
            
            position = positions[0]
            return {
                "success": True,
                "has_position": True,
                "position": position,
                "symbol": position.get("symbol"),
                "side": position.get("side"),
                "contracts": position.get("contracts"),
                "contractSize": position.get("contractSize"),
                "unrealizedPnl": position.get("unrealizedPnl"),
                "percentage": position.get("percentage"),
                "markPrice": position.get("markPrice"),
                "collateral": position.get("collateral"),
                "leverage": position.get("leverage")
            }
        except Exception as e:
            return {"success": False, "error": f"Error getting position: {str(e)}", "position": None}
    
    def validate_sl_tp_for_position(
        self,
        position: Dict[str, Any],
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        side: str
    ) -> Dict[str, Any]:
        """Validate SL/TP prices against open position."""
        try:
            if not position:
                return {"success": False, "valid": False, "errors": ["No position found"], "warnings": []}
            
            position_side = position.get("side")
            errors: List[str] = []
            warnings: List[str] = []
            
            # For LONG positions
            if position_side == "long" or side == "buy":
                if stop_loss_price >= entry_price:
                    errors.append(f"Stop loss (${stop_loss_price}) must be below entry (${entry_price})")
                if take_profit_price <= entry_price:
                    errors.append(f"Take profit (${take_profit_price}) must be above entry (${entry_price})")
                
                sl_distance_pct = ((entry_price - stop_loss_price) / entry_price) * 100
                if sl_distance_pct < 0.5:
                    warnings.append(f"Stop loss is very tight ({sl_distance_pct:.2f}% below entry)")
            
            # For SHORT positions
            elif position_side == "short" or side == "sell":
                if stop_loss_price <= entry_price:
                    errors.append(f"Stop loss (${stop_loss_price}) must be above entry (${entry_price})")
                if take_profit_price >= entry_price:
                    errors.append(f"Take profit (${take_profit_price}) must be below entry (${entry_price})")
                
                sl_distance_pct = ((stop_loss_price - entry_price) / entry_price) * 100
                if sl_distance_pct < 0.5:
                    warnings.append(f"Stop loss is very tight ({sl_distance_pct:.2f}% above entry)")
            
            return {
                "success": len(errors) == 0,
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "position_side": position_side,
            }
        
        except Exception as e:
            return {"success": False, "valid": False, "error": f"Validation error: {str(e)}", "errors": [], "warnings": []}