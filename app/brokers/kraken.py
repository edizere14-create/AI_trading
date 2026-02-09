# Kraken broker implementation

import aiohttp
import time
import base64
import hashlib
import hmac
import urllib.parse
import logging
from typing import Optional, Any
from app.brokers.base import BrokerBase

logger = logging.getLogger(__name__)

class KrakenBroker(BrokerBase):
    BASE_URL = "https://api.kraken.com/0"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    async def get_balance(self) -> float:
        """Get account balance with error handling."""
        path = "/0/private/Balance"
        url = f"{self.BASE_URL}{path}"
        nonce = str(int(time.time() * 1000))
        post_data = {"nonce": nonce}
        headers = self._get_kraken_headers(path, post_data)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=post_data, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("error"):
                        logger.error("Kraken balance error: %s", data["error"])
                        raise Exception(f"Kraken API error: {data['error']}")
                    return float(data.get("result", {}).get("ZUSD", 0))
        except aiohttp.ClientError as exc:
            logger.error("Network error fetching balance: %s", exc)
            raise
        except Exception as exc:
            logger.error("Unexpected error fetching balance: %s", exc)
            raise

    async def place_order(self, symbol: str, side: str, quantity: float, price: Optional[float] = None) -> dict[str, Any]:
        """Place order with proper validation."""
        path = "/0/private/AddOrder"
        url = f"{self.BASE_URL}{path}"
        nonce = str(int(time.time() * 1000))
        order_type = "limit" if price else "market"
        post_data = {
            "nonce": nonce,
            "pair": symbol,
            "type": side,
            "ordertype": order_type,
            "volume": str(quantity),
        }
        if price:
            post_data["price"] = str(price)
        headers = self._get_kraken_headers(path, post_data)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=post_data, headers=headers) as resp:
                    data: dict[str, Any] = await resp.json()
                    if data.get("error"):
                        logger.error("Order placement error: %s", data["error"])
                        raise Exception(f"Kraken API error: {data['error']}")
                    return data
        except aiohttp.ClientError as exc:
            logger.error("Network error placing order: %s", exc)
            raise
        except Exception as exc:
            logger.error("Unexpected error placing order: %s", exc)
            raise

    def _get_kraken_headers(self, path: str, data: dict[str, str]) -> dict[str, str]:
        """Generate Kraken authentication headers."""
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data['nonce']) + postdata).encode()
        message = path.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        sigdigest = base64.b64encode(mac.digest())
        return {
            'API-Key': self.api_key,
            'API-Sign': sigdigest.decode(),
        }

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Get order status - placeholder for now."""
        return {"order_id": order_id, "status": "filled"}

    async def get_ticker(self, symbol: str) -> float:
        """Fetch current ticker price from Kraken public API."""
        path = "/public/Ticker"
        url = f"{self.BASE_URL}{path}"
        params = {"pair": symbol}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    if data.get("error"):
                        logger.error("Ticker error for %s: %s", symbol, data["error"])
                        raise Exception(f"Kraken API error: {data['error']}")
                    
                    result = data.get("result", {})
                    if not result:
                        logger.warning("No ticker data for %s", symbol)
                        return 0.0
                    
                    pair_data = list(result.values())[0]
                    last_price = float(pair_data.get("c", [0])[0])
                    
                    if last_price <= 0:
                        logger.warning("Invalid price for %s: %s", symbol, last_price)
                        return 0.0
                    
                    return last_price
        except aiohttp.ClientError as exc:
            logger.error("Network error fetching ticker for %s: %s", symbol, exc)
            return 0.0
        except Exception as exc:
            logger.error("Unexpected error fetching ticker for %s: %s", symbol, exc)
            return 0.0
