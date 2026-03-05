# Base class for all brokers
from abc import ABC, abstractmethod
from typing import Any, Optional, Literal

OrderKind = Literal["maker", "taker"]

class BrokerBase(ABC):
	@abstractmethod
	async def get_balance(self) -> float:
		pass
	
	@abstractmethod
	async def place_order(
		self,
		symbol: str,
		side: str,
		quantity: float,
		price: Optional[float] = None,
		order_type: str = "market",
		order_kind: OrderKind = "taker",
		expected_price: Optional[float] = None,
	) -> dict[str, Any]:
		raise NotImplementedError
		
	@abstractmethod
	async def get_order_status(self, order_id: str) -> dict[str, Any]:
		pass
