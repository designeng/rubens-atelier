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
