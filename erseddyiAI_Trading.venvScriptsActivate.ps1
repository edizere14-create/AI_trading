[1mdiff --git a/app/brokers/kraken.py b/app/brokers/kraken.py[m
[1mindex 803ee7f..ecce8aa 100644[m
[1m--- a/app/brokers/kraken.py[m
[1m+++ b/app/brokers/kraken.py[m
[36m@@ -1,125 +1,153 @@[m
 # Kraken broker implementation[m
 [m
[31m-import aiohttp[m
[32m+[m[32mimport asyncio[m
[32m+[m[32mimport hmac[m
[32m+[m[32mimport hashlib[m
 import time[m
 import base64[m
[31m-import hashlib[m
[31m-import hmac[m
[31m-import urllib.parse[m
[32m+[m[32mfrom typing import Dict, List, Optional[m
[32m+[m[32mimport aiohttp[m
 import logging[m
[31m-from typing import Optional, Any[m
[31m-from app.brokers.base import BrokerBase[m
 [m
 logger = logging.getLogger(__name__)[m
 [m
[31m-class KrakenBroker(BrokerBase):[m
[31m-    BASE_URL = "https://api.kraken.com/0"[m
[31m-[m
[31m-    def __init__(self, api_key: str, api_secret: str):[m
[32m+[m[32mclass KrakenBroker:[m
[32m+[m[32m    """Enterprise Kraken integration with async support"""[m
[32m+[m[41m    [m
[32m+[m[32m    def __init__(self, api_key: str, api_secret: str, sandbox: bool = False):[m
         self.api_key = api_key[m
         self.api_secret = api_secret[m
[31m-[m
[31m-    async def get_balance(self) -> float:[m
[31m-        """Get account balance with error handling."""[m
[31m-        path = "/0/private/Balance"[m
[31m-        url = f"{self.BASE_URL}{path}"[m
[31m-        nonce = str(int(time.time() * 1000))[m
[31m-        post_data = {"nonce": nonce}[m
[31m-        headers = self._get_kraken_headers(path, post_data)[m
[32m+[m[32m        self.sandbox = sandbox[m
[32m+[m[32m        self.base_url = "https://api.kraken.com" if not sandbox else "https://api.sandbox.kraken.com"[m
[32m+[m[32m        self.session: Optional[aiohttp.ClientSession] = None[m
         [m
[31m-        try:[m
[31m-            async with aiohttp.ClientSession() as session:[m
[31m-                async with session.post(url, data=post_data, headers=headers) as resp:[m
[31m-                    data = await resp.json()[m
[31m-                    if data.get("error"):[m
[31m-                        logger.error("Kraken balance error: %s", data["error"])[m
[31m-                        raise Exception(f"Kraken API error: {data['error']}")[m
[31m-                    return float(data.get("result", {}).get("ZUSD", 0))[m
[31m-        except aiohttp.ClientError as exc:[m
[31m-            logger.error("Network error fetching balance: %s", exc)[m
[31m-            raise[m
[31m-        except Exception as exc:[m
[31m-            logger.error("Unexpected error fetching balance: %s", exc)[m
[31m-            raise[m
[31m-[m
[31m-    async def place_order(self, symbol: str, side: str, quantity: float, price: Optional[float] = None) -> dict[str, Any]:[m
[31m-        """Place order with proper validation."""[m
[31m-        path = "/0/private/AddOrder"[m
[31m-        url = f"{self.BASE_URL}{path}"[m
[31m-        nonce = str(int(time.time() * 1000))[m
[31m-        order_type = "limit" if price else "market"[m
[31m-        post_data = {[m
[31m-            "nonce": nonce,[m
[31m-            "pair": symbol,[m
[31m-            "type": side,[m
[31m-            "ordertype": order_type,[m
[31m-            "volume": str(quantity),[m
[32m+[m[32m    async def __aenter__(self):[m
[32m+[m[32m        self.session = aiohttp.ClientSession()[m
[32m+[m[32m        return self[m
[32m+[m[41m    [m
[32m+[m[32m    async def __aexit__(self, *args):[m
[32m+[m[32m        if self.session:[m
[32m+[m[32m            await self.session.close()[m
[32m+[m[41m    [m
[32m+[m[32m    def _generate_nonce(self) -> str:[m
[32m+[m[32m        """Generate nonce for Kraken API"""[m
[32m+[m[32m        return str(int(time.time() * 1000))[m
[32m+[m[41m    [m
[32m+[m[32m    def _get_kraken_signature(self, urlpath: str, data: Dict, nonce: str) -> str:[m
[32m+[m[32m        """Generate Kraken API signature"""[m
[32m+[m[32m        postdata = data.copy()[m
[32m+[m[32m        postdata['nonce'] = nonce[m
[32m+[m[32m        encoded = (str(postdata['nonce']) +[m[41m [m
[32m+[m[32m                  aiohttp.FormData(postdata)._serialize())[m
[32m+[m[41m        [m
[32m+[m[32m        message = urlpath.encode() + hashlib.sha256([m
[32m+[m[32m            encoded.encode()).digest()[m
[32m+[m[41m        [m
[32m+[m[32m        signature = base64.b64encode([m
[32m+[m[32m            hmac.new([m
[32m+[m[32m                base64.b64decode(self.api_secret),[m
[32m+[m[32m                message,[m
[32m+[m[32m                hashlib.sha512[m
[32m+[m[32m            ).digest()[m
[32m+[m[32m        )[m
[32m+[m[32m        return signature.decode()[m
[32m+[m[41m    [m
[32m+[m[32m    async def get_ticker(self, pair: str) -> Dict:[m
[32m+[m[32m        """Get current ticker data"""[m
[32m+[m[32m        params = {'pair': pair}[m
[32m+[m[32m        return await self._api_call('/0/public/Ticker', params, False)[m
[32m+[m[41m    [m
[32m+[m[32m    async def get_ohlc(self, pair: str, interval: int = 1440) -> List[List]:[m
[32m+[m[32m        """Get OHLC data (1440 = daily)"""[m
[32m+[m[32m        params = {'pair': pair, 'interval': interval}[m
[32m+[m[32m        data = await self._api_call('/0/public/OHLC', params, False)[m
[32m+[m[32m        return data.get(pair, [])[m
[32m+[m[41m    [m
[32m+[m[32m    async def place_order([m
[32m+[m[32m        self,[m
[32m+[m[32m        pair: str,[m
[32m+[m[32m        side: str,[m
[32m+[m[32m        order_type: str,[m
[32m+[m[32m        volume: float,[m
[32m+[m[32m        price: Optional[float] = None,[m
[32m+[m[32m        **kwargs[m
[32m+[m[32m    ) -> Dict:[m
[32m+[m[32m        """[m
[32m+[m[32m        Place order on Kraken[m
[32m+[m[41m        [m
[32m+[m[32m        Args:[m
[32m+[m[32m            pair: Trading pair (e.g., 'XXRPZUSD')[m
[32m+[m[32m            side: 'buy' or 'sell'[m
[32m+[m[32m            order_type: 'market', 'limit'[m
[32m+[m[32m            volume: Amount to trade[m
[32m+[m[32m            price: Limit price (required for limit orders)[m
[32m+[m[32m        """[m
[32m+[m[32m        data = {[m
[32m+[m[32m            'pair': pair,[m
[32m+[m[32m            'type': side,[m
[32m+[m[32m            'ordertype': order_type,[m
[32m+[m[32m            'volume': str(volume),[m
         }[m
[32m+[m[41m        [m
         if price:[m
[31m-            post_data["price"] = str(price)[m
[31m-        headers = self._get_kraken_headers(path, post_data)[m
[32m+[m[32m            data['price'] = str(price)[m
         [m
[31m-        try:[m
[31m-            async with aiohttp.ClientSession() as session:[m
[31m-                async with session.post(url, data=post_data, headers=headers) as resp:[m
[31m-                    data: dict[str, Any] = await resp.json()[m
[31m-                    if data.get("error"):[m
[31m-                        logger.error("Order placement error: %s", data["error"])[m
[31m-                        raise Exception(f"Kraken API error: {data['error']}")[m
[31m-                    return data[m
[31m-        except aiohttp.ClientError as exc:[m
[31m-            logger.error("Network error placing order: %s", exc)[m
[31m-            raise[m
[31m-        except Exception as exc:[m
[31m-            logger.error("Unexpected error placing order: %s", exc)[m
[31m-            raise[m
[31m-[m
[31m-    def _get_kraken_headers(self, path: str, data: dict[str, str]) -> dict[str, str]:[m
[31m-        """Generate Kraken authentication headers."""[m
[31m-        postdata = urllib.parse.urlencode(data)[m
[31m-        encoded = (str(data['nonce']) + postdata).encode()[m
[31m-        message = path.encode() + hashlib.sha256(encoded).digest()[m
[31m-        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)[m
[31m-        sigdigest = base64.b64encode(mac.digest())[m
[31m-        return {[m
[31m-  