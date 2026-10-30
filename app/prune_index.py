"""Drop expired task ids from the index: `python -m app.prune_index` (make prune-index).

One-off cleanup for ids that piled up in `harness:tasks` before expiry removed them.
"""

import asyncio

from redis.asyncio import Redis

from .config import settings
from .store import TaskStore


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        print(f"removed {await TaskStore(redis).prune_orphaned_index()} expired ids from the task index")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
