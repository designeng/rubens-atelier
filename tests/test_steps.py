from datetime import timedelta
from types import SimpleNamespace

from app.db import Artifact, utcnow
from app.steps import latest_artifacts


async def _add(db, *rows):
    start = utcnow()
    async with db.session() as s:
        for i, (name, content) in enumerate(rows):
            s.add(Artifact(item_id="i1", name=name, content=content, created_at=start + timedelta(seconds=i)))
        await s.commit()


async def test_error_is_dropped_once_a_later_artifact_exists(db):
    await _add(db, ("plan", "p"), ("error", "implement: boom"), ("implement", "fixed"))
    assert await latest_artifacts(SimpleNamespace(db=db), "i1") == {"plan": "p", "implement": "fixed"}


async def test_error_is_kept_for_the_retry_that_follows_it(db):
    await _add(db, ("plan", "p"), ("implement", "v1"), ("error", "review: boom"))
    assert await latest_artifacts(SimpleNamespace(db=db), "i1") == {
        "plan": "p", "implement": "v1", "error": "review: boom",
    }
