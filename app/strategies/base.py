# Base class for all strategies
from abc import ABC, abstractmethod

class StrategyBase(ABC):
	@abstractmethod
	def generate_signals(self, data):
		pass

	@abstractmethod
	def on_order_filled(self, order):
		pass
