# Backtest service for running strategies on historical data
from typing import Any
from app.strategies.base import BaseStrategy



class BacktestService:
	def __init__(self, strategy: BaseStrategy, initial_balances: dict[str, float] | None = None):
		self.strategy = strategy
		self.initial_balances = initial_balances or {"USD": 10000.0}

	def run(self, historical_data: list[dict[str, Any]]) -> dict[str, Any]:
		# historical_data should be a list of dicts, each with asset info, e.g. {'asset': 'BTC', 'close': 42000, ...}
		balances = self.initial_balances.copy()
		positions = {asset: 0.0 for asset in balances if asset != "USD"}
		signals = []
		trades = []
		for i in range(1, len(historical_data)):
			data_slice = historical_data[:i+1]
			signal = self.strategy.generate_signals(data_slice)
			signals.append(signal)
			asset = historical_data[i].get('asset', 'BTC')
			price = historical_data[i]['close']
			# Simulate order execution
			if signal and signal.get('action') == 'buy' and positions.get(asset, 0) == 0:
				qty = balances["USD"] // price
				if qty > 0:
					positions[asset] = qty
					balances["USD"] -= qty * price
					trades.append({'type': 'buy', 'asset': asset, 'qty': qty, 'price': price, 'time': historical_data[i]['time']})
			elif signal and signal.get('action') == 'sell' and positions.get(asset, 0) > 0:
				balances["USD"] += positions[asset] * price
				trades.append({'type': 'sell', 'asset': asset, 'qty': positions[asset], 'price': price, 'time': historical_data[i]['time']})
				positions[asset] = 0
		# Calculate final portfolio value
		final_value = balances["USD"]
		for asset, qty in positions.items():
			if qty > 0:
				# Find last price for asset
				last_price = next((d['close'] for d in reversed(historical_data) if d.get('asset', 'BTC') == asset), None)
				if last_price:
					final_value += qty * last_price
		pnl = final_value - sum(self.initial_balances.values())

		# Advanced analytics
		values = []
		running_bal = balances["USD"]
		running_positions = positions.copy()
		for i in range(1, len(historical_data)):
			asset = historical_data[i].get('asset', 'BTC')
			price = historical_data[i]['close']
			val = running_bal
			for a, qty in running_positions.items():
				if qty > 0:
					last_price = price if a == asset else next((d['close'] for d in reversed(historical_data[:i+1]) if d.get('asset', 'BTC') == a), None)
					if last_price:
						val += qty * last_price
			values.append(val)

		# Max drawdown
		peak = values[0] if values else self.initial_balances["USD"]
		max_drawdown = 0
		for v in values:
			if v > peak:
				peak = v
			drawdown = (peak - v) / peak if peak else 0
			if drawdown > max_drawdown:
				max_drawdown = drawdown

		# Sharpe ratio
		import math
		returns = [0] + [math.log(values[i]/values[i-1]) for i in range(1, len(values)) if values[i-1] > 0]
		avg_return = sum(returns) / len(returns) if returns else 0
		std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns)) if returns else 0
		sharpe = (avg_return / std_return) * math.sqrt(252) if std_return else 0

		# Trade stats
		num_trades = len(trades)
		num_wins = sum(1 for t in trades if t['type'] == 'sell' and t['price'] > t.get('entry_price', 0))
		num_losses = num_trades - num_wins

		return {
			'signals': signals,
			'trades': trades,
			'final_balances': balances,
			'final_value': final_value,
			'pnl': pnl,
			'max_drawdown': max_drawdown,
			'sharpe_ratio': sharpe,
			'num_trades': num_trades,
			'num_wins': num_wins,
			'num_losses': num_losses
		}
