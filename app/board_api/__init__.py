"""HTTP API of the factory: projects (tenants), work items, the Kanban board and the inbox."""

from fastapi import APIRouter

from . import items, projects, scheduler, workspaces
from .deps import Db
from .board_page import page
from .projects import DONE_LIMIT_MAX
from .schemas import Orm

router = APIRouter()
router.include_router(projects.router)
router.include_router(items.router)
router.include_router(workspaces.router)
router.include_router(scheduler.router)

__all__ = ["DONE_LIMIT_MAX", "Db", "Orm", "page", "router"]
