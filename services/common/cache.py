"""Redis caching manager with toggleable support via CACHE_ENABLED."""

import json
import logging
import os
from typing import Any

import redis

logger = logging.getLogger("cache")


class CacheClient:
    """Manages caching via Redis with fallback when disabled or unavailable."""

    def __init__(self, redis_url: str | None = None, enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = os.getenv("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")

        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client: redis.Redis | None = None
        self._in_memory: dict[str, str] = {}

        if self.enabled:
            try:
                self._client = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                )
                # Test connectivity
                self._client.ping()
            except Exception as exc:
                logger.warning(
                    "Could not connect to Redis at %s: %s. Falling back to local cache.",
                    self.redis_url,
                    exc,
                )
                # Keep cache enabled via local in-memory store so cache hits/misses work without crash
                self._client = None

    def get(self, key: str) -> Any | None:
        """Fetch and deserialize a JSON cached value."""
        if not self.enabled:
            return None
        try:
            if self._client:
                data = self._client.get(key)
            else:
                data = self._in_memory.get(key)
            return json.loads(data) if data is not None else None
        except Exception as exc:
            logger.error("Cache get error for key %s: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Serialize and cache a value with a TTL in seconds."""
        if not self.enabled:
            return False
        try:
            encoded = json.dumps(value)
            if self._client:
                return bool(self._client.set(key, encoded, ex=ttl))
            self._in_memory[key] = encoded
            return True
        except Exception as exc:
            logger.error("Cache set error for key %s: %s", key, exc)
            return False

    def clear(self) -> None:
        """Clear cached entries."""
        try:
            if self._client:
                self._client.flushdb()
            self._in_memory.clear()
        except Exception as exc:
            logger.error("Cache clear error: %s", exc)
