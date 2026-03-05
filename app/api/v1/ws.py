from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.websocket_service import manager

router = APIRouter(tags=["ws"])


@router.websocket("/api/v1/ws/market")
async def market_ws(
    websocket: WebSocket,
    topics: str = Query(default="market,pnl,trades,execution"),
):
    requested_topics = {t.strip() for t in (topics or "").split(",") if t.strip()}
    if not requested_topics:
        requested_topics = {"market"}

    await manager.connect(websocket, topics=requested_topics)
    await websocket.send_json(
        {
            "type": "heartbeat",
            "topic": "market",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        while True:
            msg = await websocket.receive_text()
            if not msg:
                continue

            try:
                payload = json.loads(msg)
            except Exception:
                continue

            action = str(payload.get("action", "")).lower()
            topic_set = {str(t).strip() for t in payload.get("topics", []) if str(t).strip()}
            if action == "subscribe" and topic_set:
                await manager.subscribe(websocket, topic_set)
                await websocket.send_json({"type": "subscribed", "topics": sorted(topic_set)})
            elif action == "unsubscribe" and topic_set:
                await manager.unsubscribe(websocket, topic_set)
                await websocket.send_json({"type": "unsubscribed", "topics": sorted(topic_set)})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        return


@router.websocket("/ws/price")
async def legacy_price_ws(websocket: WebSocket):
    await manager.connect(websocket, topics={"market"})
    await websocket.send_json({"type": "heartbeat", "ts": datetime.now(timezone.utc).isoformat()})
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat", "ts": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        return