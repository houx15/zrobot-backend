"""WebSocket connection manager"""

from typing import Dict, Optional
from fastapi import WebSocket
import json
import asyncio
from datetime import datetime, timezone

from app.websocket.protocol import ServerMessage
from app.redis_client import redis_client


class ConnectionManager:
    """
    Manages WebSocket connections for AI conversations.

    Each conversation has at most one active connection.
    Connections are tracked by conversation_id.
    """

    def __init__(self):
        # conversation_id -> WebSocket
        self.active_connections: Dict[int, WebSocket] = {}
        # conversation_id -> user_id
        self.connection_users: Dict[int, int] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        conversation_id: int,
        user_id: int,
    ) -> bool:
        """
        Accept a new WebSocket connection.

        Returns True if connection was successful, False otherwise.
        """
        async with self._lock:
            # Check if there's already an active connection for this conversation
            if conversation_id in self.active_connections:
                # Close the old connection
                old_ws = self.active_connections[conversation_id]
                try:
                    await old_ws.close(code=1000, reason="New connection established")
                except Exception:
                    pass

            await websocket.accept()
            self.active_connections[conversation_id] = websocket
            self.connection_users[conversation_id] = user_id

            # Update Redis session with connection status
            await redis_client.hset(
                f"conv:session:{conversation_id}",
                "ws_connected",
                "true",
            )
            await redis_client.hset(
                f"conv:session:{conversation_id}",
                "last_active_at",
                datetime.now(timezone.utc).isoformat(),
            )

            return True

    async def disconnect(self, conversation_id: int) -> None:
        """
        Handle WebSocket disconnection.
        """
        async with self._lock:
            if conversation_id in self.active_connections:
                del self.active_connections[conversation_id]
            if conversation_id in self.connection_users:
                del self.connection_users[conversation_id]

            # Update Redis session
            await redis_client.hset(
                f"conv:session:{conversation_id}",
                "ws_connected",
                "false",
            )

    def get_connection(self, conversation_id: int) -> Optional[WebSocket]:
        """Get WebSocket connection for a conversation"""
        return self.active_connections.get(conversation_id)

    def get_user_id(self, conversation_id: int) -> Optional[int]:
        """Get user_id for a conversation"""
        return self.connection_users.get(conversation_id)

    def is_connected(self, conversation_id: int) -> bool:
        """Check if conversation has an active connection"""
        return conversation_id in self.active_connections

    async def send_message(
        self,
        conversation_id: int,
        message: ServerMessage,
    ) -> bool:
        """
        Send a message to a specific conversation.

        Returns True if message was sent, False if connection not found.
        """
        websocket = self.active_connections.get(conversation_id)
        if websocket:
            try:
                await websocket.send_json(message.model_dump())
                return True
            except Exception:
                # Connection might be closed
                await self.disconnect(conversation_id)
                return False
        return False

    async def send_json(
        self,
        conversation_id: int,
        data: dict,
    ) -> bool:
        """
        Send raw JSON data to a specific conversation.

        Returns True if data was sent, False if connection not found.
        """
        websocket = self.active_connections.get(conversation_id)
        if websocket:
            try:
                await websocket.send_json(data)
                return True
            except Exception:
                await self.disconnect(conversation_id)
                return False
        return False

    async def send_bytes(
        self,
        conversation_id: int,
        data: bytes,
    ) -> bool:
        """
        Send raw bytes to a specific conversation.

        Returns True if data was sent, False if connection not found.
        """
        websocket = self.active_connections.get(conversation_id)
        if websocket:
            try:
                await websocket.send_bytes(data)
                return True
            except Exception:
                await self.disconnect(conversation_id)
                return False
        return False

    async def broadcast_to_all(self, message: ServerMessage) -> None:
        """
        Broadcast a message to all active connections.
        """
        disconnected = []
        for conversation_id, websocket in self.active_connections.items():
            try:
                await websocket.send_json(message.model_dump())
            except Exception:
                disconnected.append(conversation_id)

        # Clean up disconnected
        for conv_id in disconnected:
            await self.disconnect(conv_id)

    def get_active_count(self) -> int:
        """Get number of active connections"""
        return len(self.active_connections)

    def get_all_conversation_ids(self) -> list:
        """Get all active conversation IDs"""
        return list(self.active_connections.keys())


# Global connection manager instance
connection_manager = ConnectionManager()
