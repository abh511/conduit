"""Async Redis client.

Like the DB engine, the client holds a connection pool and connects lazily.
"""

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> "Redis":
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return client
