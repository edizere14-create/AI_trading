# Kraken broker implementation

import asyncio
import hmac
import hashlib
import time
import base64
from typing import Dict, List, Optional
import aiohttp
import logging

logger = logging.getLogger(__name__)

class KrakenBroker:
    """Enterprise Kraken integration with async support"""
    
    def __init__(self, api_key: str, api_secret: str, sandbox: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        self.base_url = "https://api.kraken.com" if not sandbox else "https://api.sandbox.kraken.com"
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
    
    def _generate_nonce(self) -> str:
        """Generate nonce for Kraken API"""
        return str(int(time.time() * 1000))
    
    def _get_kraken_signature(self, urlpath: str, data: Dict, nonce: str) -> str:
        """Generate Kraken API signature"""
        postdata = data.copy()
        postdata['nonce'] = nonce
        encoded = (str(postdata['nonce']) + 
                  aiohttp.FormData(postdata)._serialize())
        
        message = urlpath.encode() + hashlib.sha256(
            encoded.encode()).digest()
        
        signature = base64.b64encode(
            hmac.new(
                base64.b64decode(self.api_secret),
                message,
                hashlib.sha512
            ).digest()
        )
        return signature.decode()
    
    async def get_ticker(self, pair: str) -> Dict:
        """Get current ticker data"""
        params = {'pair': pair}
        return await self._api_call('/0/public/Ticker', params, False)
    
    async def get_ohlc(self, pair: str, interval: int = 1440) -> List[List]:
        """Get OHLC data (1440 = daily)"""
        params = {'pair': pair, 'interval': interval}
        data = await self._api_call('/0/public/OHLC', params, False)
        return data.get(pair, [])
    
    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        volume: float,
        price: Optional[float] = None,
        **kwargs
    ) -> Dict:
        """
        Place order on Kraken
        
        Args:
            pair: Trading pair (e.g., 'XXRPZUSD')
            side: 'buy' or 'sell'
            order_type: 'market', 'limit'
            volume: Amount to trade
            price: Limit price (required for limit orders)
        """
        data = {
            'pair': pair,
            'type': side,
            'ordertype': order_type,
            'volume': str(volume),
        }
        
        if price:
            data['price'] = str(price)
        
        data.update(kwargs)
        
        result = await self._api_call('/0/private/AddOrder', data, True)
        logger.info(f"Order placed: {side} {volume} {pair}")
        return result
    
    async def cancel_order(self, txid: str) -> Dict:
        """Cancel open order"""
        data = {'txid': txid}
        return await self._api_call('/0/private/CancelOrder', data, True)
    
    async def get_open_orders(self) -> Dict:
        """Get all open orders"""
        return await self._api_call('/0/private/OpenOrders', {}, True)
    
    async def get_closed_orders(self) -> Dict:
        """Get closed orders history"""
        return await self._api_call('/0/private/ClosedOrders', {}, True)
    
    async def get_balance(self) -> Dict:
        """Get account balance"""
        return await self._api_call('/0/private/Balance', {}, True)
    
    async def _api_call(
        self,
        endpoint: str,
        data: Dict,
        private: bool = False
    ) -> Dict:
        """Make API call to Kraken"""
        try:
            if private:
                if not self.api_key or not self.api_secret:
                    raise ValueError("API credentials required for private endpoint")
                
                nonce = self._generate_nonce()
                data['nonce'] = nonce
                
                headers = {
                    'API-Sign': self._get_kraken_signature(endpoint, data, nonce),
                    'API-Key': self.api_key,
                }
                
                async with self.session.post(
                    f"{self.base_url}{endpoint}",
                    data=data,
                    headers=headers
                ) as resp:
                    return await resp.json()
            else:
                async with self.session.get(
                    f"{self.base_url}{endpoint}",
                    params=data
                ) as resp:
                    return await resp.json()
                    
        except Exception as e:
            logger.error(f"Kraken API error: {str(e)}")
            raise
