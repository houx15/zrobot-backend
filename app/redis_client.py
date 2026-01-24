import redis.asyncio as redis
from typing import Optional
import json

from app.config import settings


class RedisClient:
    """Async Redis client wrapper"""

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def connect(self) -> None:
        """Initialize Redis connection"""
        self._client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def close(self) -> None:
        """Close Redis connection"""
        if self._client:
            await self._client.close()

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("Redis client not initialized. Call connect() first.")
        return self._client

    # String operations
    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        return await self.client.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        return await self.client.delete(*keys)

    async def exists(self, key: str) -> bool:
        return await self.client.exists(key) > 0

    # Hash operations
    async def hset(self, name: str, key: str, value: str) -> int:
        return await self.client.hset(name, key, value)

    async def hget(self, name: str, key: str) -> Optional[str]:
        return await self.client.hget(name, key)

    async def hgetall(self, name: str) -> dict:
        return await self.client.hgetall(name)

    async def hdel(self, name: str, *keys: str) -> int:
        return await self.client.hdel(name, *keys)

    async def hmset(self, name: str, mapping: dict) -> bool:
        return await self.client.hset(name, mapping=mapping)

    # List operations
    async def rpush(self, name: str, *values: str) -> int:
        return await self.client.rpush(name, *values)

    async def lpop(self, name: str) -> Optional[str]:
        return await self.client.lpop(name)

    async def lrange(self, name: str, start: int, end: int) -> list:
        return await self.client.lrange(name, start, end)

    async def llen(self, name: str) -> int:
        return await self.client.llen(name)

    # Set operations
    async def sadd(self, name: str, *values: str) -> int:
        return await self.client.sadd(name, *values)

    async def srem(self, name: str, *values: str) -> int:
        return await self.client.srem(name, *values)

    async def smembers(self, name: str) -> set:
        return await self.client.smembers(name)

    # TTL operations
    async def expire(self, name: str, time: int) -> bool:
        return await self.client.expire(name, time)

    async def ttl(self, name: str) -> int:
        return await self.client.ttl(name)

    # Pub/Sub operations
    async def publish(self, channel: str, message: str) -> int:
        return await self.client.publish(channel, message)

    # JSON helpers
    async def set_json(self, key: str, data: dict, ex: Optional[int] = None) -> bool:
        return await self.set(key, json.dumps(data, ensure_ascii=False), ex=ex)

    async def get_json(self, key: str) -> Optional[dict]:
        value = await self.get(key)
        if value:
            return json.loads(value)
        return None


# Global Redis client instance
redis_client = RedisClient()


async def get_redis() -> RedisClient:
    """Dependency for getting Redis client"""
    return redis_client
