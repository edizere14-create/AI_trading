from fastapi import WebSocket
from typing import Dict, List

class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, symbol: str) -> None:
        await websocket.accept()
        if symbol not in self.active_connections:
            self.active_connections[symbol] = []
        self.active_connections[symbol].append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        for symbol in self.active_connections:
            if websocket in self.active_connections[symbol]:
                self.active_connections[symbol].remove(websocket)

    async def broadcast(self, message: str) -> None:
        for symbol_connections in self.active_connections.values():
            for connection in symbol_connections:
                await connection.send_text(message)

manager = ConnectionManager()