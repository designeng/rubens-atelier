"""Dependencies shared by the board routers: the database, engine and scheduler of the app."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from ..db import Database, Project
from ..engine import Engine, EngineError
from ..scheduler import Scheduler

def get_db(request: Request) -> Database:
    return request.app.state.db


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


Db = Annotated[Database, Depends(get_db)]
Eng = Annotated[Engine, Depends(get_engine)]
Sched = Annotated[Scheduler, Depends(get_scheduler)]


async def _project(db: Database, slug: str) -> Project:
    async with db.session() as s:
        project = await s.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _engine_error(e: EngineError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if "not found" in str(e) else status.HTTP_409_CONFLICT
    return HTTPException(code, str(e))
