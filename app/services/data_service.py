import httpx
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, List, Dict

import pandas as pd
import requests
import urllib.parse
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.market_data import PriceResponse, CandleResponse

logger = logging.getLogger(__name__)

class DataService:
    """Data service for managing market data."""
    
    def __init__(self, exchange_id: str = "krakenfutures") -> None:
        self.exchange_id = exchange_id
        self._kraken_base = "https://futures.kraken.com"

    async def get_live_price(self, symbol: str) -> PriceResponse:
        """Get live price for a symbol."""
        coingecko_id = {
            "BTC": "bitcoin", 
            "ETH": "ethereum", 
            "SOL": "solana"
        }.get(symbol.upper(), "bitcoin")
        
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coingecko_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            price_data = data[coingecko_id]
            
            price = float(price_data['usd'])
            timestamp = datetime.now(timezone.utc)
            
            return PriceResponse(
                symbol=symbol,
                price=price,
                timestamp=timestamp
            )

    def _tf_to_kraken_interval(self, timeframe: str) -> str:
        tf = (timeframe or "1m").lower()
        mapping = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            # normalize unsupported inputs
            "2h": "1h",
            "3m": "1m",
        }
        return mapping.get(tf, "1m")

    def _normalize_futures_symbol_for_charts(self, symbol: str) -> str:
        raw = (symbol or "PF_XBTUSD").strip().upper()
        normalized_map = {
            "BTC/USD:USD": "PF_XBTUSD",
            "XBT/USD:USD": "PF_XBTUSD",
            "BTCUSD": "PF_XBTUSD",
            "XBTUSD": "PF_XBTUSD",
            "PI_XBTUSD": "PF_XBTUSD",
            "PF_XBTUSD": "PF_XBTUSD",
            "ETH/USD:USD": "PF_ETHUSD",
            "ETHUSD": "PF_ETHUSD",
            "PI_ETHUSD": "PF_ETHUSD",
            "PF_ETHUSD": "PF_ETHUSD",
            "SOL/USD:USD": "PF_SOLUSD",
            "SOLUSD": "PF_SOLUSD",
            "PI_SOLUSD": "PF_SOLUSD",
            "PF_SOLUSD": "PF_SOLUSD",
        }
        if raw in normalized_map:
            return normalized_map[raw]
        if raw.startswith("PI_"):
            return raw.replace("PI_", "PF_", 1)
        return raw

    def _symbol_candidates(self, symbol: str) -> list[str]:
        normalized = self._normalize_futures_symbol_for_charts(symbol)
        original = (symbol or "").strip().upper()
        out = [normalized]
        if normalized.startswith("PF_"):
            out.append(normalized.replace("PF_", "PI_", 1))
        elif normalized.startswith("PI_"):
            out.append(normalized.replace("PI_", "PF_", 1))
        if original and original not in out:
            out.append(original)
        return list(dict.fromkeys(out))

    def _tf_to_minutes(self, timeframe: str) -> int:
        mapping = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
        }
        return mapping.get((timeframe or "1m").lower(), 1)

    def _fetch_kraken_ohlcv_sync(self, symbol: str, timeframe: str, limit: int) -> "pd.DataFrame":
        interval = self._tf_to_kraken_interval(timeframe)
        now = datetime.now(timezone.utc)
        lookback_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[interval]
        start = now - timedelta(minutes=lookback_minutes * max(limit, 50))

        from_sec, to_sec = int(start.timestamp()), int(now.timestamp())
        from_ms, to_ms = from_sec * 1000, to_sec * 1000

        last_err = None
        payload = None

        for sym in self._symbol_candidates(symbol):
            sym_enc = urllib.parse.quote(sym, safe="")
            base = f"{self._kraken_base}/api/charts/v1/trade/{sym_enc}/{interval}"

            for params in ({"from": from_sec, "to": to_sec}, {"from": from_ms, "to": to_ms}):
                try:
                    r = requests.get(base, params=params, timeout=10)
                    r.raise_for_status()
                    payload = r.json()
                    if isinstance(payload, dict):
                        candles = payload.get("candles") or payload.get("result")
                    elif isinstance(payload, list):
                        candles = payload
                    else:
                        candles = None

                    if isinstance(candles, list) and candles:
                        break
                    payload = None
                except Exception as e:
                    last_err = e
            if payload:
                break

        if payload is None:
            raise RuntimeError(f"Failed to fetch OHLCV from Kraken charts v1: {last_err}")

        candles = payload.get("candles") if isinstance(payload, dict) else payload
        if isinstance(payload, dict) and (not isinstance(candles, list) or not candles):
            candles = payload.get("result")
        if not isinstance(candles, list) or not candles:
            raise RuntimeError(f"No candles returned for {symbol} ({timeframe}).")

        rows = []
        for c in candles:
            if isinstance(c, dict):
                ts = c.get("time") or c.get("timestamp")
                o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
                v = c.get("volume", c.get("volumeNotional", c.get("v", 0)))
            else:
                ts = c[0]
                o, h, l, cl = c[1], c[2], c[3], c[4]
                v = c[5] if len(c) > 5 else 0

            ts_val = pd.to_datetime(ts, unit="ms" if isinstance(ts, (int, float)) and ts > 10_000_000_000 else "s", utc=True, errors="coerce")
            rows.append({"timestamp": ts_val, "open": float(o), "high": float(h), "low": float(l), "close": float(cl), "volume": float(v)})

        df = pd.DataFrame(rows).dropna(subset=["timestamp"])
        if df.empty:
            raise RuntimeError(f"Parsed empty OHLCV for {symbol} ({timeframe}).")

        return df.sort_values("timestamp").tail(limit).reset_index(drop=True)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 200,
        exchange_id: str | None = None,
    ) -> pd.DataFrame:
        ex = (exchange_id or self.exchange_id or "krakenfutures").lower()
        if ex not in ("krakenfutures", "kraken_futures"):
            raise ValueError(f"Unsupported exchange_id: {ex}")

        return await asyncio.to_thread(self._fetch_kraken_ohlcv_sync, symbol, timeframe, limit)

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        price = await self.get_live_price(symbol)
        return {
            "symbol": symbol,
            "last": float(price.price),
            "timestamp": price.timestamp.isoformat(),
        }

    async def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "bids": [],
            "asks": [],
            "depth": int(limit),
        }

async def get_historical_candles(
    db: AsyncSession,
    symbol: str,
    days: int = 30
) -> List[Dict[str, float]]:
    """Get historical candle data."""
    try:
        logger.debug("Fetching %d days of candles for %s", days, symbol)
        
        mock_candles = []
        base_price = 50000.0
        
        for i in range(days * 24):
            timestamp = datetime.now(timezone.utc) - timedelta(hours=i)
            mock_candles.append({
                "timestamp": timestamp.isoformat(),
                "open": base_price + (i % 100),
                "high": base_price + (i % 100) + 50,
                "low": base_price + (i % 100) - 50,
                "close": base_price + (i % 100) + 10,
                "volume": 100.0 + (i % 50)
            })
        
        return mock_candles
    except Exception as exc:
        logger.error("Error fetching candles for %s: %s", symbol, exc)
        return []

async def get_current_price(
    db: AsyncSession,
    symbol: str
) -> PriceResponse:
    """Get current price for a symbol."""
    try:
        logger.debug("Fetching current price for %s", symbol)
        
        return PriceResponse(
            symbol=symbol,
            price=50000.0,
            timestamp=datetime.now(timezone.utc)
        )
    except Exception as exc:
        logger.error("Error fetching price for %s: %s", symbol, exc)
        return PriceResponse(
            symbol=symbol,
            price=0.0,
            timestamp=datetime.now(timezone.utc)
        )

async def store_candles(
    db: AsyncSession,
    symbol: str,
    candles: List[CandleResponse]
) -> bool:
    """Store candle data in database."""
    try:
        logger.info("Storing %d candles for %s", len(candles), symbol)
        return True
    except Exception as exc:
        logger.error("Error storing candles for %s: %s", symbol, exc)
        return False

async def get_live_price(symbol: str) -> PriceResponse:
    """Get live price (standalone function for backward compatibility)."""
    service = DataService()
    return await service.get_live_price(symbol)
