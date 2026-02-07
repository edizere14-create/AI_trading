# Kraken data pipeline utility
import aiohttp
import datetime

async def fetch_kraken_ohlcv(symbol: str, interval: int = 60, since: int = None, limit: int = 100):
	"""
	Fetch OHLCV data from Kraken public API.
	symbol: Kraken pair (e.g., 'XXBTZUSD')
	interval: Timeframe in minutes
	since: Unix timestamp (seconds)
	limit: Max candles to fetch
	"""
	url = f"https://api.kraken.com/0/public/OHLC?pair={symbol}&interval={interval}"
	if since:
		url += f"&since={since}"
	async with aiohttp.ClientSession() as session:
		async with session.get(url) as resp:
			data = await resp.json()
			ohlc = data['result'][symbol]
			# Format: [time, open, high, low, close, vwap, volume, count]
			candles = []
			for row in ohlc[-limit:]:
				candles.append({
					'time': datetime.datetime.utcfromtimestamp(row[0]),
					'open': float(row[1]),
					'high': float(row[2]),
					'low': float(row[3]),
					'close': float(row[4]),
					'volume': float(row[6]),
				})
			return candles
