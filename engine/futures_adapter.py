"""Exchange connectivity and initialization."""
from typing import Any, Dict, Optional
import ccxt  # type: ignore


def initialize_exchange(api_key: str, api_secret: str, env: str) -> ccxt.Exchange:
    """Create and return a Kraken Spot or Futures (Demo) exchange instance."""
    if env == "Futures (Demo)":
        exchange: ccxt.Exchange = ccxt.krakenfutures({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
            "urls": {"api": {"public": "https://demo-futures.kraken.com/api"}},
        })
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
        balance = exchange.fetch_balance()
        server_time = exchange.fetch_time()
        
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
    except ccxt.NetworkError as e:
        return {"success": False, "error": f"Network error: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


def connect_kraken(
    api_key: str,
    api_secret: str,
    env: str
) -> tuple[Optional[ccxt.Exchange], Optional[Dict[str, Any]], Optional[int | float | Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Legacy function for backward compatibility."""
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