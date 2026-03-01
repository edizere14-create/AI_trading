from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import streamlit as st

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover
    websocket = None


@dataclass
class Tick:
    price: float
    ts: str


class PriceStream:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self.latest = Tick(price=0.0, ts=datetime.now(timezone.utc).isoformat())
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        if self.ws_url.startswith("kraken://"):
            self._started = True
            t = threading.Thread(target=self._run_kraken_ws, daemon=True)
            t.start()
            return

        if websocket is None:
            return

        self._started = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _kraken_product_id(self, symbol: str) -> str:
        raw = (symbol or "").strip().upper()
        if raw.startswith("PI_"):
            return raw
        base_map = {"BTC": "XBT"}
        if "/" in raw:
            base_quote = raw.split(":", 1)[0]
            parts = base_quote.split("/")
            if len(parts) == 2:
                base, quote = parts
                base = base_map.get(base, base)
                return f"PI_{base}{quote}"
        return os.getenv("KRAKEN_FUTURES_PRODUCT_ID", "PI_XBTUSD").strip().upper() or "PI_XBTUSD"

    def _poll_kraken(self) -> None:
        try:
            import ccxt  # type: ignore
        except Exception:
            return

        api_key = os.getenv("KRAKEN_API_KEY", "").strip()
        api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
        symbol = self.ws_url.replace("kraken://", "", 1).strip() or os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD")

        exchange = ccxt.krakenfutures(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 30000,
            }
        )
        demo = os.getenv("KRAKEN_FUTURES_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"}
        exchange.set_sandbox_mode(demo)

        while True:
            try:
                ticker = exchange.fetch_ticker(symbol)
                px = float(ticker.get("last") or 0.0)
                ts = datetime.now(timezone.utc).isoformat()
                if px > 0:
                    self.latest = Tick(price=px, ts=ts)
            except Exception:
                pass
            time.sleep(2)

    def _run_kraken_ws(self) -> None:
        if websocket is None:
            self._poll_kraken()
            return

        symbol = self.ws_url.replace("kraken://", "", 1).strip() or os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD")
        product_id = self._kraken_product_id(symbol)
        demo = os.getenv("KRAKEN_FUTURES_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"}
        ws_endpoint = "wss://demo-futures.kraken.com/ws/v1" if demo else "wss://futures.kraken.com/ws/v1"

        def on_open(ws_app) -> None:
            msg = {
                "event": "subscribe",
                "feed": "ticker",
                "product_ids": [product_id],
            }
            ws_app.send(json.dumps(msg))

        def on_message(_, message: str) -> None:
            try:
                payload = json.loads(message)
            except Exception:
                return
            if not isinstance(payload, dict):
                return
            if str(payload.get("feed", "")).lower() != "ticker":
                return
            if str(payload.get("product_id", "")).upper() != product_id:
                return
            try:
                px = float(payload.get("last") or payload.get("price") or 0.0)
            except Exception:
                return
            if px <= 0:
                return
            ts_raw = payload.get("time") or payload.get("timestamp")
            ts = str(ts_raw) if ts_raw else datetime.now(timezone.utc).isoformat()
            self.latest = Tick(price=px, ts=ts)

        def on_error(_, __) -> None:
            return

        def on_close(_, __, ___) -> None:
            return

        ws = websocket.WebSocketApp(
            ws_endpoint,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(reconnect=3)

    def _run(self) -> None:
        def on_message(_, message: str) -> None:
            try:
                payload = json.loads(message)
                px = float(payload.get("price"))
                ts = str(payload.get("ts") or payload.get("timestamp") or datetime.now(timezone.utc).isoformat())
            except Exception:
                try:
                    px = float(message)
                    ts = datetime.now(timezone.utc).isoformat()
                except Exception:
                    return
            self.latest = Tick(price=px, ts=ts)

        def on_error(_, __):
            return

        def on_close(_, __, ___):
            return

        ws = websocket.WebSocketApp(self.ws_url, on_message=on_message, on_error=on_error, on_close=on_close)
        ws.run_forever(reconnect=3)


@st.cache_resource
def get_price_stream(ws_url: str) -> PriceStream:
    return PriceStream(ws_url)