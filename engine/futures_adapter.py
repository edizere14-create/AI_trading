"""Exchange connectivity and initialization."""
from typing import Any, Dict, Optional
import time  # ADD THIS LINE
import ccxt  # type: ignore


def initialize_exchange(api_key: str, api_secret: str, env: str) -> ccxt.Exchange:
    """Create and return a Kraken Spot or Futures (Demo) exchange instance."""
    if env == "Futures (Demo)":
        exchange: ccxt.Exchange = ccxt.krakenfutures({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        })
        exchange.set_sandbox_mode(True)
    else:
        exchange = ccxt.kraken({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        })
    return exchange


def test_connection(exchange: ccxt.Exchange) -> Dict[str, Any]:
    """Test exchange connection and fetch initial data."""
    try:
        exchange.check_required_credentials()
        markets = exchange.load_markets()
        
        # Skip both balance and time fetch - not supported by Kraken Futures
        server_time = int(time.time() * 1000)  # Use system time instead
        balance = {
            "free": {},
            "used": {},
            "total": {},
        }
        
        return {
            "success": True,
            "exchange": exchange,
            "balance": balance,
            "server_time": server_time,
            "markets": markets,
            "symbols": exchange.symbols,
            "error": ""
        }
    except ccxt.AuthenticationError as e:
        return {"success": False, "error": f"Authentication failed: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


def connect_kraken(
    api_key: str,
    api_secret: str,
    env: str
) -> tuple[Optional[ccxt.Exchange], Optional[Dict[str, Any]], Optional[int | float | Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Connect to Kraken REST API."""
    if not api_key or not api_secret:
        return None, None, None, None, "API key and secret are required"
    
    try:
        exchange = initialize_exchange(api_key, api_secret, env)
        result = test_connection(exchange)
        
        if result["success"]:
            return (
                result["exchange"],
                result["balance"],
                result["server_time"],
                result["markets"],
                ""
            )
        else:
            return None, None, None, None, result["error"]
    except Exception as e:
        return None, None, None, None, f"Unexpected error: {str(e)}"