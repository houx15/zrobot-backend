from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Query, Path
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, close_db
from app.redis_client import redis_client
from app.api.v1.router import api_router
from app.utils.exceptions import APIException, api_exception_handler, generic_exception_handler
from app.websocket.handler import websocket_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    print(f"Starting {settings.app_name}...")

    # Initialize Redis
    await redis_client.connect()
    print("Redis connected")

    # Note: In production, you would use Alembic for migrations
    # instead of creating tables directly
    # await init_db()

    yield

    # Shutdown
    print("Shutting down...")
    await redis_client.close()
    await close_db()


def create_app() -> FastAPI:
    """Create FastAPI application"""
    app = FastAPI(
        title=settings.app_name,
        description="AI智慧学习平板 - 学生端API",
        version="1.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # Store debug setting for exception handlers
    app.debug = settings.debug

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify actual origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add exception handlers
    app.add_exception_handler(APIException, api_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Include API routers
    app.include_router(api_router, prefix="/api/v1/student")

    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "app": settings.app_name}

    # WebSocket endpoint for AI conversation
    @app.websocket("/ws/conversation/{conversation_id}")
    async def ws_conversation(
        websocket: WebSocket,
        conversation_id: int = Path(..., description="Conversation ID"),
        token: str = Query(..., description="WebSocket authentication token"),
    ):
        """WebSocket endpoint for AI real-time conversation"""
        await websocket_endpoint(websocket, conversation_id, token)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
