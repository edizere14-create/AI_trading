from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

        self._redis_enabled = False
        self._redis_channel_prefix = "realtime"
        self._redis_client = None
        self._redis_pubsub = None
        self._redis_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._init_redis_if_available()

    async def stop(self) -> None:
        if self._redis_task:
            self._redis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._redis_task
            self._redis_task = None

        if self._redis_pubsub is not None:
            with contextlib.suppress(Exception):
                await self._redis_pubsub.close()
            self._redis_pubsub = None

        if self._redis_client is not None:
            with contextlib.suppress(Exception):
                await self._redis_client.close()
            self._redis_client = None

    async def _init_redis_if_available(self) -> None:
        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            logger.info("Realtime hub: REDIS_URL not set, using in-process pub/sub")
            return

        try:
            from redis.asyncio import from_url as redis_from_url

            self._redis_client = redis_from_url(redis_url, decode_responses=True)
            self._redis_pubsub = self._redis_client.pubsub()
            await self._redis_pubsub.psubscribe(f"{self._redis_channel_prefix}:*")
            self._redis_task = asyncio.create_task(self._redis_listener_loop())
            self._redis_enabled = True
            logger.info("Realtime hub: Redis pub/sub enabled")
        except Exception as exc:
            self._redis_enabled = False
            self._redis_client = None
            self._redis_pubsub = None
            logger.warning("Realtime hub: Redis unavailable, falling back to in-process pub/sub (%s)", exc)

    async def _redis_listener_loop(self) -> None:
        if self._redis_pubsub is None:
            return

        while True:
            msg = await self._redis_pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not msg:
                await asyncio.sleep(0.05)
                continue

            try:
                channel = str(msg.get("channel") or "")
                data = msg.get("data")
                payload = json.loads(data) if isinstance(data, str) else data
                topic = channel.split(":", 1)[1] if ":" in channel else "market"
                await self._broadcast_payload(topic=topic, payload=payload)
            except Exception:
                logger.exception("Realtime hub: failed to process Redis message")

    async def connect(self, websocket: WebSocket, topics: set[str] | None = None) -> None:
        await websocket.accept()
        topics = topics or {"market"}
        async with self._lock:
            self._connections[websocket] = set(topics)

    async def subscribe(self, websocket: WebSocket, topics: set[str]) -> None:
        async with self._lock:
            if websocket in self._connections:
                self._connections[websocket].update(topics)

    async def unsubscribe(self, websocket: WebSocket, topics: set[str]) -> None:
        async with self._lock:
            if websocket in self._connections:
                self._connections[websocket].difference_update(topics)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)

    async def publish(self, topic: str, event_type: str, data: dict[str, Any]) -> None:
        payload = {
            "type": event_type,
            "topic": topic,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        payload.update(data)

        if self._redis_enabled and self._redis_client is not None:
            channel = f"{self._redis_channel_prefix}:{topic}"
            await self._redis_client.publish(channel, json.dumps(payload))
            return

        await self._broadcast_payload(topic=topic, payload=payload)

    async def _broadcast_payload(self, topic: str, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        async with self._lock:
            items = list(self._connections.items())

        for connection, topics in items:
            if topic not in topics and "all" not in topics:
                continue
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)

        if stale:
            async with self._lock:
                for connection in stale:
                    self._connections.pop(connection, None)


manager = ConnectionManager()