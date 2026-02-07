# Example mean reversion strategy implementation
from app.strategies.base import StrategyBase

class MeanReversionStrategy(StrategyBase):
	def generate_signals(self, data):
		# Simple mean reversion: buy if price < moving average, sell if price > moving average
		if len(data) < 5:
			return None
		closes = [bar['close'] for bar in data[-5:]]
		avg = sum(closes) / len(closes)
		last = closes[-1]
		if last < avg:
			return {"action": "buy"}
		elif last > avg:
			return {"action": "sell"}
		return {"action": "hold"}

	def on_order_filled(self, order):
		# Handle post-order logic
		pass
