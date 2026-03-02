import asyncio
import logging
import os
import time
from typing import Any, Dict, Literal, Optional

from kraken.spot import Market, Trade, User

logger = logging.getLogger(__name__)

OrderKind = Literal["maker", "taker"]


class KrakenBroker:
    """Kraken broker using python-kraken-sdk behind an async-compatible wrapper."""

    def __init__(self, api_key: str, api_secret: str, sandbox: bool = False, base_url: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        self.base_url = (
            base_url
            or os.getenv("KRAKEN_BASE_URL", "").strip()
            or "https://demo-futures.kraken.com/derivatives/api/v3/"
        )

        self._market_client = self._build_client(Market)
        self._trade_client = self._build_client(Trade)
        self._user_client = self._build_client(User)

    def _build_client(self, client_cls: type) -> Any:
        init_attempts = (
            {"key": self.api_key, "secret": self.api_secret},
            {"api_key": self.api_key, "secret": self.api_secret},
            {"key": self.api_key, "secret": self.api_secret, "url": self.base_url},
            {"api_key": self.api_key, "secret": self.api_secret, "url": self.base_url},
        )
        for kwargs in init_attempts:
            try:
                return client_cls(**kwargs)
            except TypeError:
                continue
        return client_cls()

    async def __aenter__(self) -> "KrakenBroker":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @staticmethod
    def _extract_first(items: Any) -> Any:
        if isinstance(items, list) and items:
            return items[0]
        return items

    @staticmethod
    def _as_result(payload: Any) -> Any:
        if isinstance(payload, dict) and "result" in payload:
            return payload.get("result")
        return payload

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _invoke(self, client: Any, method_names: list[str], **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for method_name in method_names:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                return method(**kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise AttributeError(f"No compatible sdk method found: {method_names}")

    async def get_ticker(self, pair: str) -> float:
        payload = await asyncio.to_thread(
            self._invoke,
            self._market_client,
            ["get_ticker_information", "get_ticker", "ticker"],
            pair=pair,
            symbol=pair,
        )
        data = self._as_result(payload)

        if isinstance(data, dict):
            if pair in data and isinstance(data[pair], dict):
                ticker_obj = data[pair]
                if isinstance(ticker_obj.get("c"), list) and ticker_obj.get("c"):
                    price = self._to_float(ticker_obj["c"][0])
                    if price is not None:
                        return price
                for key in ("last", "lastPrice", "markPrice", "price"):
                    price = self._to_float(ticker_obj.get(key))
                    if price is not None:
                        return price

            for key in ("price", "last", "lastPrice", "markPrice"):
                price = self._to_float(data.get(key))
                if price is not None:
                    return price

        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                for key in ("price", "last", "lastPrice", "markPrice"):
                    price = self._to_float(first.get(key))
                    if price is not None:
                        return price

        return 0.0

    async def get_ohlc(self, pair: str, interval: int = 1440) -> list[list[Any]]:
        payload = await asyncio.to_thread(
            self._invoke,
            self._market_client,
            ["get_ohlc_data", "get_ohlc", "ohlc"],
            pair=pair,
            symbol=pair,
            interval=interval,
        )
        data = self._as_result(payload)
        if isinstance(data, dict):
            pair_data = data.get(pair)
            if isinstance(pair_data, list):
                return pair_data
        if isinstance(data, list):
            return data
        return []

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "market",
        order_kind: OrderKind = "taker",
        expected_price: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        pair = kwargs.pop("pair", symbol)
        volume = kwargs.pop("volume", quantity)
        t0 = time.time()

        data: dict[str, Any] = {
            "pair": pair,
            "symbol": pair,
            "side": side,
            "type": side,
            "order_type": order_type,
            "ordertype": order_type,
            "volume": str(volume),
            "size": volume,
            "size_type": "contract",
        }

        if price is not None:
            data["price"] = str(price)

        if order_kind == "maker":
            data["ordertype"] = "limit"
            data["order_type"] = "limit"
            data.setdefault("oflags", "post")

        data.update(kwargs)

        result = await asyncio.to_thread(
            self._invoke,
            self._trade_client,
            ["create_order", "add_order", "place_order"],
            **data,
        )
        logger.info("Order placed: %s %s %s", side, volume, pair)

        txid = None
        if isinstance(result, dict):
            raw_result = result.get("result", result)
            if isinstance(raw_result, dict):
                txid = self._extract_first(raw_result.get("txid")) or raw_result.get("order_id") or raw_result.get("id")

        slippage = None
        if expected_price and price:
            exp = float(expected_price)
            px = float(price)
            raw = (px - exp) / exp if exp else 0.0
            slippage = raw if str(side).lower() == "buy" else -raw

        return {
            "status": "submitted",
            "order_id": txid,
            "filled_quantity": 0.0,
            "avg_fill_price": None,
            "metrics": {
                "slippage": slippage,
                "fill_rate": 0.0,
                "latency_ms": (time.time() - t0) * 1000.0,
            },
            "raw": result,
        }

    async def cancel_order(self, txid: str) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._invoke,
            self._trade_client,
            ["cancel_order", "cancel_open_order"],
            txid=txid,
            order_id=txid,
        )

    async def get_open_orders(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._invoke,
            self._trade_client,
            ["get_open_orders", "open_orders"],
        )

    async def get_closed_orders(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._invoke,
            self._trade_client,
            ["get_closed_orders", "closed_orders"],
        )

    async def get_balance(self) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._invoke,
            self._user_client,
            ["get_account_balance", "get_balance", "get_balances"],
        )

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        result = await asyncio.to_thread(
            self._invoke,
            self._trade_client,
            ["get_order", "query_orders_info", "get_order_status"],
            txid=order_id,
            order_id=order_id,
        )

        order_info = None
        if isinstance(result, dict):
            payload = result.get("result", result)
            if isinstance(payload, dict):
                order_info = payload.get(order_id) or payload

        if not order_info:
            return {"status": "not_found", "raw": result}

        raw_status = order_info.get("status")
        filled_qty = self._to_float(order_info.get("vol_exec", order_info.get("filled", 0.0)))
        avg_fill = self._to_float(order_info.get("price"))

        status_map = {
            "open": "pending",
            "closed": "filled",
            "canceled": "cancelled",
            "cancelled": "cancelled",
            "expired": "cancelled",
        }
        status = status_map.get(raw_status, raw_status or "pending")

        if status == "pending" and filled_qty and filled_qty > 0:
            status = "partial"

        return {
            "status": status,
            "filled_quantity": filled_qty,
            "avg_fill_price": avg_fill,
            "raw": order_info,
        }
