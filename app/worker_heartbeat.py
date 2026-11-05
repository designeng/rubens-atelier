"""Which Celery workers are alive, for the dashboard.

Each worker's main process refreshes a short-lived key in Redis from a background thread (so a
worker busy running agents still reports in); the API only reads them. A key that is not
refreshed within `TTL` seconds is a worker that is gone.
"""

import json
import logging
import os
import socket
import threading
import time

from redis import Redis as SyncRedis
from redis.asyncio import Redis

PREFIX = "harness:workers:"
TTL = 30
INTERVAL = 10

log = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, redis_url: str, concurrency: int | None = None):
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.redis = SyncRedis.from_url(redis_url, decode_responses=True)
        self.concurrency = concurrency
        self.started_at = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="worker-heartbeat", daemon=True)

    def start(self) -> None:
        self._beat()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.redis.delete(PREFIX + self.worker_id)
        except Exception:
            pass

    def _beat(self) -> None:
        info = {"id": self.worker_id, "concurrency": self.concurrency, "started_at": self.started_at}
        try:
            self.redis.set(PREFIX + self.worker_id, json.dumps(info), ex=TTL)
        except Exception:
            log.warning("could not publish the worker heartbeat", exc_info=True)

    def _run(self) -> None:
        while not self._stop.wait(INTERVAL):
            self._beat()


async def alive(redis: Redis) -> list[dict]:
    """The workers that reported in recently, oldest first."""
    keys = [k async for k in redis.scan_iter(match=PREFIX + "*")]
    raws = await redis.mget(keys) if keys else []
    return sorted((json.loads(r) for r in raws if r), key=lambda w: w["started_at"])
