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
# Run column → `kind` label of harness_run_tokens_sum.
TOKENS = {
    "input_tokens": "input",
    "cache_read_tokens": "cache_read",
    "cache_write_tokens": "cache_write",
    "output_tokens": "output",
}


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
                select(
                    Run.status,
                    Run.cost_usd,
                    Run.created_at,
                    Run.finished_at,
                    Run.failed_tools,
                    *(getattr(Run, column) for column in TOKENS),
                ).where(Run.created_at >= utcnow() - WINDOW)
            )
        ).all()
    runs = Counter(r[0] for r in rows)
    cost = sum(r[1] or 0.0 for r in rows)
    durations = [(aware(r[3]) - aware(r[2])).total_seconds() for r in rows if r[3] is not None]
    failed_tools = sum(int(r[4] or 0) for r in rows)
    tokens = {kind: sum(int(r[5 + i] or 0) for r in rows) for i, kind in enumerate(TOKENS.values())}
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
        "harness_run_tokens_sum", f"Tokens of factory runs {window}, by kind.", "gauge",
        [({"kind": k}, v) for k, v in tokens.items()],
    )
    lines += _fmt(
        "harness_run_failed_tool_calls_sum", f"Failed tool calls of factory runs {window}.", "gauge",
        [({}, failed_tools)],
    )
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
