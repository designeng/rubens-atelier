"""Celery wiring. Redis is the broker; task state lives in TaskStore, so no result backend.

Run:
  celery -A app.celery_app worker -l info --concurrency 2
  celery -A app.celery_app beat -l info        # orphan reaper + issue poller
"""

import asyncio
import logging
import os
import socket

from celery import Celery
from celery.signals import worker_process_init
from redis.asyncio import Redis

from .config import settings

log = logging.getLogger(__name__)

celery_app = Celery("agent_harness", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    # Agent runs are long and expensive: take one at a time per process, and only ack
    # after the run so a crashed worker's message is redelivered. Redelivery never
    # re-runs the agent — execute_task's claim turns it into a `worker lost` failure.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Must exceed the longest run, or Redis redelivers messages that are still running.
    broker_transport_options={"visibility_timeout": settings.task_timeout_seconds + 900},
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    beat_schedule={
        "reap-orphans": {"task": "harness.reap_orphans", "schedule": max(settings.lease_ttl_seconds, 30)},
    },
)
if settings.issue_repos:
    celery_app.conf.beat_schedule["poll-issues"] = {
        "task": "harness.poll_issues",
        "schedule": settings.issue_poll_seconds,
    }


@worker_process_init.connect
def _scrub_secrets(**_):
    # Settings are already loaded; drop the GitHub token from the env the agent's
    # subprocesses (Claude Code, its Bash tool, verify commands) inherit.
    os.environ.pop("HARNESS_GITHUB_TOKEN", None)


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def _with_store(fn):
    from .store import TaskStore

    redis = _redis()
    try:
        return await fn(TaskStore(redis))
    finally:
        await redis.aclose()


@celery_app.task(name="harness.run_agent", time_limit=settings.task_timeout_seconds + 300)
def run_agent(task_id: str) -> None:
    from .execution import execute_task

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    asyncio.run(_with_store(lambda store: execute_task(task_id, store, worker_id)))


@celery_app.task(name="harness.reap_orphans")
def reap_orphans() -> list[str]:
    from .execution import reap_orphans as reap

    reaped = asyncio.run(_with_store(reap))
    if reaped:
        log.warning("marked orphaned tasks as failed: %s", reaped)
    return reaped


@celery_app.task(name="harness.poll_issues")
def poll_issues() -> list[str]:
    from .service import poll_issues as poll

    return asyncio.run(_with_store(poll))
