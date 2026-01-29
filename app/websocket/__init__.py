"""WebSocket module for AI real-time conversation"""

from app.websocket.manager import ConnectionManager, connection_manager
from app.websocket.handler import websocket_endpoint
from app.websocket.protocol import WsEnvelope, ServerMessage, ConversationState

__all__ = [
    "ConnectionManager",
    "connection_manager",
    "websocket_endpoint",
    "WsEnvelope",
    "ServerMessage",
    "ConversationState",
]
