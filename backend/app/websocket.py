"""WebSocket connection manager for real-time updates."""

from __future__ import annotations
import json
import asyncio
import logging
from typing import Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message_type: str, data: Any):
        payload = json.dumps({"type": message_type, "data": data})
        disconnected = []
        for conn in list(self.active_connections):
            try:
                await conn.send_text(payload)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.disconnect(conn)


ws_manager = ConnectionManager()
