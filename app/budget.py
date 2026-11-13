"""Spending limits: what an item (with everything below it) and a project have cost so far.

Spent money is the sum of `Run.cost_usd`; a run without a cost (it died before reporting)
counts as zero. An item's limit covers its own runs and those of its descendants, so an
epic's budget caps all of its stories. A new agent run may only start while every limit on
the way up (the item, its ancestors, the project) still has money left.
"""

from sqlalchemy import func, select

from .db import Project, Run, WorkItem


class Spending:
    """Spent money of one project: per item (including descendants) and in total."""

    def __init__(self, own: dict[str, float], parents: dict[str, str | None]):
        self.parents = parents
        self.items = {item_id: 0.0 for item_id in parents}
        for item_id, cost in own.items():
            node: str | None = item_id
            while node is not None and node in self.items:
                self.items[node] += cost
                node = parents.get(node)
        self.project = sum(own.values())

    def item(self, item_id: str) -> float:
        return self.items.get(item_id, 0.0)

    def chain(self, item_id: str) -> list[str]:
        """The item and its ancestors, nearest first."""
        ids, node, seen = [], item_id, set()
        while node is not None and node not in seen and node in self.parents:
            ids.append(node)
            seen.add(node)
            node = self.parents[node]
        return ids


async def project_spending(s, project_id: str) -> Spending:
    """Two queries, however many items the project has."""
    items = await s.execute(select(WorkItem.id, WorkItem.parent_id).where(WorkItem.project_id == project_id))
    parents = dict(items.all())
    rows = await s.execute(
        select(Run.item_id, func.coalesce(func.sum(Run.cost_usd), 0.0))
        .join(WorkItem, WorkItem.id == Run.item_id)
        .where(WorkItem.project_id == project_id)
        .group_by(Run.item_id)
    )
    return Spending({item_id: float(cost) for item_id, cost in rows}, parents)


async def remaining(s, item: WorkItem, project: Project) -> tuple[float | None, str | None]:
    """The money left under the tightest limit that applies to `item`, and which limit that is
    ("project" or "item <title>"); (None, None) when nothing is limited."""
    spending = await project_spending(s, project.id)
    left: tuple[float, str] | None = None
    if project.budget_usd is not None:
        left = (project.budget_usd - spending.project, "project")
    for item_id in spending.chain(item.id):
        node = item if item_id == item.id else await s.get(WorkItem, item_id)
        if node.budget_usd is None:
            continue
        candidate = (node.budget_usd - spending.item(item_id), f"{node.kind} {node.title!r}")
        if left is None or candidate[0] < left[0]:
            left = candidate
    return left if left else (None, None)


MIN_SAMPLES = 3


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated quantile of an ascending, non-empty list."""
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


async def _story_costs(s, succeeded, project_id: str | None) -> list[float]:
    """What each succeeded story or task cost in total (epics left out), of one project or all."""
    query = (
        select(func.coalesce(func.sum(Run.cost_usd), 0.0))
        .select_from(WorkItem)
        .join(Run, Run.item_id == WorkItem.id)
        .where(WorkItem.kind != "epic", succeeded)
        .group_by(WorkItem.id)
    )
    if project_id is not None:
        query = query.where(WorkItem.project_id == project_id)
    return sorted(float(c) for c in await s.scalars(query))


async def forecast(s, project: Project, succeeded, open_items: list[WorkItem], stories: int, run_cap: float) -> dict:
    """What `stories` new stories will likely cost, judged by what finished stories cost, and how
    that fits the project's budget next to what its open stories will likely still spend.
    `succeeded` is the SQL condition for a succeeded item (Engine.states_where)."""
    costs = await _story_costs(s, succeeded, project.id) if succeeded is not None else []
    basis = "project"
    if len(costs) < MIN_SAMPLES and succeeded is not None:
        costs, basis = await _story_costs(s, succeeded, None), "all projects"
    spending = await project_spending(s, project.id)
    out: dict = {
        "basis": basis,
        "samples": len(costs),
        "per_run_cap_usd": run_cap,
        "spent_usd": round(spending.project, 2),
        "budget_usd": project.budget_usd,
    }
    if not costs:
        out["note"] = (
            "No story has succeeded yet, so there is nothing to go by. A story runs several agent steps "
            f"(plan, implement, review, …), each capped at ${run_cap:g}."
        )
        return out
    median, p80 = _quantile(costs, 0.5), _quantile(costs, 0.8)
    # An open story has already spent some of what it will cost; count only what is likely left.
    open_left = sum(max(0.0, median - spending.item(i.id)) for i in open_items)
    out |= {
        "per_story_usd": {"median": round(median, 2), "p80": round(p80, 2), "max": round(costs[-1], 2)},
        "open_stories": len(open_items),
        "open_stories_left_usd": round(open_left, 2),
    }
    if stories:
        out["new_stories"] = {
            "count": stories,
            "likely_usd": round(median * stories, 2),
            "p80_usd": round(p80 * stories, 2),
        }
    if project.budget_usd is not None:
        free = project.budget_usd - spending.project - open_left
        out["budget_left_usd"] = round(project.budget_usd - spending.project, 2)
        out["free_after_open_usd"] = round(free, 2)
        out["stories_that_fit"] = max(0, int(free // median)) if median > 0 else None
        if stories and median * stories > free:
            out["warning"] = (
                f"{stories} new stories likely cost ${median * stories:.2f}, but only ${max(free, 0):.2f} "
                "is free once the open stories finish. Raise the project budget or plan fewer stories."
            )
    return out
