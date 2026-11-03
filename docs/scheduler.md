# Scheduler

Agent steps do not go to Celery right away; they wait in a priority queue: higher item `priority` first, FIFO on ties. A step is handed out only when a slot is free: at most `HARNESS_MAX_AGENTS` in total and at most `HARNESS_MAX_AGENTS_PER_PROJECT` per project. Both limits can be changed on the fly, in the board header (**slots**, **per project**) or via `PATCH /scheduler/limits`; the override lives in Redis and applies to every dispatcher. A raised limit hands out waiting steps right away; agents above a lowered one finish their step. The pace follows the subscription windows that Claude Code reports through `rate_limit` events (status, utilization, reset time):

- `rejected` or utilization ≥ `HARNESS_QUOTA_PAUSE_AT` → no new steps until the window resets;
- utilization ≥ `HARNESS_QUOTA_THROTTLE_AT` or `allowed_warning` → one agent;
- weekly windows are not spent faster than the week goes by: allowed usage is the elapsed share of the week plus `HARNESS_QUOTA_PACE_SLACK`.

**force** (board header, or `force: true` in `PATCH /scheduler/limits`) keeps the slot limit through the drop to one agent and past `HARNESS_QUOTA_PAUSE_AT`. Only a window Claude Code reports as `rejected` still stops dispatch: runs would only hit the limit and go back to the queue.

A run that hits the limit is not a work failure. The step returns to the queue without escalation and continues the same session after the window resets. Action and check steps do not use Claude and bypass the scheduler. Celery worker concurrency must be at least `HARNESS_MAX_AGENTS` (and any limit set on the fly); in compose it is 6.

## API

| Method | Path | |
|---|---|---|
| `GET` | `/scheduler` | Busy slots, queue, capacity and its reason, subscription window state |
| `POST` | `/scheduler/pause`, `/scheduler/resume` | Stop or resume handing out new agent steps; running ones finish |
| `PATCH` | `/scheduler/limits` | `{max_agents?, max_agents_per_project?, force?}`: change the slot limits without a restart; `DELETE` returns to the settings |
