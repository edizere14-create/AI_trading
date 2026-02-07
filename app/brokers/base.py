# Base class for all brokers
from abc import ABC, abstractmethod

class BrokerBase(ABC):
	@abstractmethod
	async def get_balance(self):
		pass

	@abstractmethod
	async def place_order(self, symbol: str, side: str, quantity: float, price: float = None):
		pass

	@abstractmethod
	async def get_order_status(self, order_id: str):
		pass
