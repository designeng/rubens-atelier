"""Prometheus text metrics for `GET /metrics`, computed from Redis and Postgres on each scrape.

Workers keep no counters of their own: the `runs` table is the record, so the API
and the workers can't disagree.
"""

from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from .celery_app import celery_app
from .db import Database, Run, aware, utcnow
from .store import TaskStore

WINDOW = timedelta(hours=24)
TASKS_SAMPLE = 200  # newest tasks counted in harness_tasks


def _fmt(name: str, help_: str, type_: str, samples: list[tuple[dict[str, str], float]]) -> list[str]:
    lines = [f"# HELP {name} {help_}", f"# TYPE {name} {type_}"]
    for labels, value in samples:
        label_text = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
        lines.append(f"{name}{label_text} {value}")
    return lines


async def collect(store: TaskStore, db: Database) -> str:
    queue = celery_app.conf.task_default_queue
    depth = await store.r.llen(queue)
    tasks = Counter(t.status.value for t in await store.list_tasks(limit=TASKS_SAMPLE))

    async with db.session() as s:
        rows = (
            await s.execute(
                select(Run.status, Run.cost_usd, Run.created_at, Run.finished_at).where(
                    Run.created_at >= utcnow() - WINDOW
                )
            )
        ).all()
    runs = Counter(status for status, *_ in rows)
    cost = sum(c or 0.0 for _, c, _, _ in rows)
    durations = [(aware(f) - aware(c)).total_seconds() for _, _, c, f in rows if f is not None]
    finished = runs["succeeded"] + runs["failed"]
    ratio = runs["succeeded"] / finished if finished else 0.0

    window = "in the last 24 hours"
    lines: list[str] = []
    lines += _fmt("harness_queue_depth", "Messages waiting in the Celery queue.", "gauge", [({}, depth)])
    lines += _fmt(
        "harness_tasks", f"Harness tasks by status among the newest {TASKS_SAMPLE}.", "gauge",
        [({"status": k}, v) for k, v in sorted(tasks.items())],
    )
    lines += _fmt(
        "harness_runs", f"Factory runs by status {window}.", "gauge",
        [({"status": k}, v) for k, v in sorted(runs.items())],
    )
    lines += _fmt("harness_run_cost_usd_sum", f"Cost of factory runs {window}.", "gauge", [({}, round(cost, 6))])
    lines += _fmt(
        "harness_run_duration_seconds_sum", f"Total duration of finished runs {window}.", "gauge",
        [({}, round(sum(durations), 3))],
    )
    lines += _fmt(
        "harness_run_duration_seconds_count", f"Number of finished runs {window}.", "gauge", [({}, len(durations))]
    )
    lines += _fmt(
        "harness_run_success_ratio", f"Succeeded share of succeeded and failed runs {window}; 0 if none.", "gauge",
        [({}, round(ratio, 6))],
    )
    return "\n".join(lines) + "\n"
