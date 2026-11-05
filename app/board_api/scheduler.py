"""The scheduler: status, pause and resume, concurrency limits."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import worker_heartbeat
from .deps import Sched

router = APIRouter()


class Pause(BaseModel):
    reason: str = ""


@router.get("/scheduler")
async def scheduler_status(scheduler: Sched) -> dict:
    """Slots in use, queue, the subscription windows the pacing is based on, and the live workers."""
    return await scheduler.status() | {"workers": await worker_heartbeat.alive(scheduler.r)}


@router.post("/scheduler/pause")
async def pause_scheduler(body: Pause, scheduler: Sched) -> dict:
    """Stop dispatching new agent steps; running ones finish."""
    await scheduler.pause(body.reason)
    return await scheduler.status()


@router.post("/scheduler/resume")
async def resume_scheduler(scheduler: Sched) -> dict:
    await scheduler.resume()
    return await scheduler.status()


class Limits(BaseModel):
    max_agents: int | None = Field(default=None, ge=1, le=64)
    max_agents_per_project: int | None = Field(default=None, ge=1, le=64)
    force: bool | None = Field(
        default=None,
        description="Keep max_agents while subscription windows throttle or stop dispatch, unless one is rejected",
    )


@router.patch("/scheduler/limits")
async def set_scheduler_limits(body: Limits, scheduler: Sched) -> dict:
    """Change how many agents run at once, without a restart. Omitted fields keep their value."""
    await scheduler.set_limits(body.max_agents, body.max_agents_per_project, body.force)
    return await scheduler.status()


@router.delete("/scheduler/limits")
async def reset_scheduler_limits(scheduler: Sched) -> dict:
    """Drop the overrides: back to HARNESS_MAX_AGENTS and HARNESS_MAX_AGENTS_PER_PROJECT."""
    await scheduler.reset_limits()
    return await scheduler.status()
