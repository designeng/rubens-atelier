from sqlalchemy import inspect, text

from app.config import settings
from app.db import Database


async def test_create_all_adds_columns_missing_from_an_older_database():
    db = Database(settings.database_url)
    async with db.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE projects (id VARCHAR(32) PRIMARY KEY, installation_id BIGINT)"))
        await conn.execute(text("CREATE TABLE work_items (id VARCHAR(32) PRIMARY KEY, model VARCHAR(100))"))
        await conn.execute(text("INSERT INTO work_items (id) VALUES ('old')"))

    await db.create_all()

    async with db.engine.connect() as conn:
        columns = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("work_items")})
        assert "blocked_by" in columns
        # Rows from before the column hold NULL, which reads as "no blockers".
        assert (await conn.execute(text("SELECT blocked_by FROM work_items"))).scalar() is None
    await db.dispose()
