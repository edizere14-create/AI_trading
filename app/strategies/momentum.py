# Example momentum strategy implementation
from app.strategies.base import StrategyBase

class MomentumStrategy(StrategyBase):
	def generate_signals(self, data):
		# Simple momentum: buy if last close > previous close
		if len(data) < 2:
			return None
		if data[-1]['close'] > data[-2]['close']:
			return {"action": "buy"}
		elif data[-1]['close'] < data[-2]['close']:
			return {"action": "sell"}
		return {"action": "hold"}

	def on_order_filled(self, order):
		# Handle post-order logic
		pass
