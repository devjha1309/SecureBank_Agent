"""TTL metadata store. Redis is required for multi-worker deployments."""

import json
import time
from threading import RLock

from redis import Redis

from core.config.settings import get_settings
from core.errors import BankError


class Store:
    def __init__(self):
        url = get_settings().redis_url
        self.redis = Redis.from_url(url, decode_responses=True) if url else None
        self.data: dict[str, tuple[float, str]] = {}
        self.lock = RLock()

    def put(self, key: str, value: dict, ttl: int = 3600) -> None:
        try:
            if self.redis:
                self.redis.setex(key, ttl, json.dumps(value))
            else:
                with self.lock:
                    self.data = {k: v for k, v in self.data.items() if v[0] > time.time()}
                    self.data[key] = (time.time() + ttl, json.dumps(value))
        except Exception:
            raise BankError(
                "SESSION_STORE_UNAVAILABLE", "Session storage is unavailable. Please try again.", 503, True
            ) from None

    def get(self, key: str) -> dict | None:
        try:
            if self.redis:
                value = self.redis.get(key)
            else:
                entry = self.data.get(key)
                value = entry[1] if entry and entry[0] > time.time() else None
            return json.loads(value) if value else None
        except Exception:
            raise BankError("SESSION_STORE_UNAVAILABLE", "Please sign in again later.", 503, True) from None

    def delete(self, key: str) -> None:
        if self.redis:
            self.redis.delete(key)
        else:
            self.data.pop(key, None)

    def rate(self, key: str, limit: int, window: int = 60) -> None:
        bucket = f"rate:{key}:{int(time.time()) // window}"
        if self.redis:
            try:
                count = self.redis.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1,
                    bucket,
                    window,
                )
            except Exception:
                raise BankError("RATE_STORE_UNAVAILABLE", "Please try again later.", 503, True) from None
        else:
            with self.lock:
                entry = self.get(bucket) or {"count": 0}
                count = entry["count"] + 1
                self.put(bucket, {"count": count}, window)
        if count > limit:
            raise BankError("RATE_LIMITED", "Too many requests. Please wait a minute.", 429)


store = Store()
