from datetime import timedelta

from app import memory
from app.db import Note, Project, utcnow


def _note(client, slug, title, tags=(), **kw):
    body = {"title": title, "content": f"{title}: details", "tags": list(tags), **kw}
    r = client.post(f"/projects/{slug}/notes", json=body)
    assert r.status_code == 201
    return r.json()


def test_notes_crud_and_review(client):
    client.post("/projects", json={"slug": "api", "repo": "acme/api"})
    assert client.post("/projects/missing/notes", json={"title": "t", "content": "c"}).status_code == 404
    assert client.post("/projects/api/notes", json={"title": "", "content": "c"}).status_code == 422

    note = _note(client, "api", "Use uv", tags=[" Python ", "python", "CI"])
    assert (note["status"], note["created_by"], note["tags"]) == ("active", "user", ["python", "ci"])

    async def propose():
        async with client.app.state.db.session() as s:
            await memory.propose(s, note["project_id"], None, "reviewer", [{"title": "Lock files", "content": "x"}])
            await s.commit()

    client.portal.call(propose)
    (proposed,) = client.get("/projects/api/notes", params={"status": "proposed"}).json()
    assert proposed["created_by"] == "agent:reviewer"
    assert [n["title"] for n in client.get("/projects/api/notes", params={"tag": "CI"}).json()] == ["Use uv"]

    r = client.patch(f"/notes/{proposed['id']}", json={"status": "active", "content": "Commit uv.lock."})
    assert (r.json()["status"], r.json()["content"]) == ("active", "Commit uv.lock.")
    assert client.patch(f"/notes/{proposed['id']}", json={"status": "gone"}).status_code == 422
    assert client.patch(f"/notes/{proposed['id']}", json={"title": None}).status_code == 422
    assert client.patch("/notes/nope", json={"status": "archived"}).status_code == 404

    client.patch(f"/notes/{note['id']}", json={"status": "archived"})
    assert [n["title"] for n in client.get("/projects/api/notes", params={"status": "active"}).json()] == ["Lock files"]
    assert client.delete(f"/notes/{note['id']}").status_code == 204
    assert client.delete(f"/notes/{note['id']}").status_code == 404
    assert len(client.get("/projects/api/notes").json()) == 1


def test_copy_a_fragment_between_projects(client):
    for slug in ("api", "web", "cli"):
        client.post("/projects", json={"slug": slug, "repo": f"acme/{slug}"})
    uv = _note(client, "api", "Use uv", tags=["python"])
    _note(client, "api", "Pytest markers", tags=["python", "tests"])
    pr = _note(client, "api", "Small PRs", tags=["process"])
    _note(client, "api", "Ruff only", tags=["python"])

    async def propose():
        async with client.app.state.db.session() as s:
            draft = {"title": "Draft", "content": "x", "tags": ["python"]}
            await memory.propose(s, uv["project_id"], None, "planner", [draft])
            await s.commit()

    client.portal.call(propose)
    proposed_id = client.get("/projects/api/notes", params={"status": "proposed"}).json()[0]["id"]

    assert client.post("/projects/web/notes/copy", json={"from_project": "api"}).status_code == 422
    assert client.post("/projects/api/notes/copy", json={"from_project": "api", "tags": ["x"]}).status_code == 422
    assert client.post("/projects/web/notes/copy", json={"from_project": "nope", "tags": ["x"]}).status_code == 404
    r = client.post("/projects/web/notes/copy", json={"from_project": "api", "note_ids": [proposed_id]})
    assert r.status_code == 404  # only vetted notes travel

    body = {"from_project": "api", "tags": ["python"], "note_ids": [pr["id"]]}
    res = client.post("/projects/web/notes/copy", json=body).json()
    assert res["skipped"] == 0
    assert sorted(n["title"] for n in res["copied"]) == ["Pytest markers", "Ruff only", "Small PRs", "Use uv"]
    copy = next(n for n in res["copied"] if n["title"] == "Use uv")
    assert (copy["status"], copy["created_by"], copy["origin_note_id"]) == ("active", "copy:api", uv["id"])

    # Copying again skips what is there, even after the copy was edited or archived.
    client.patch(f"/notes/{copy['id']}", json={"content": "Use uv, pinned in .python-version.", "status": "archived"})
    assert client.post("/projects/web/notes/copy", json={"from_project": "api", "tags": ["python"]}).json() == {
        "copied": [], "skipped": 3
    }
    # A copy of a copy keeps the first origin, so it is not copied back where it came from.
    res = client.post("/projects/cli/notes/copy", json={"from_project": "web", "tags": ["process"]}).json()
    assert res["copied"][0]["origin_note_id"] == pr["id"]
    back = client.post("/projects/api/notes/copy", json={"from_project": "cli", "tags": ["process"]}).json()
    assert back == {"copied": [], "skipped": 1}


def test_board_counts_proposed_notes(client):
    client.post("/projects", json={"slug": "api", "repo": "acme/api"})
    assert client.get("/projects/api/board").headers["x-proposed-notes"] == "0"
    project_id = _note(client, "api", "Use uv")["project_id"]

    async def propose():
        async with client.app.state.db.session() as s:
            drafts = [{"title": "Lock files", "content": "x"}, {"title": "Small PRs", "content": "y"}]
            await memory.propose(s, project_id, None, "reviewer", drafts)
            await s.commit()

    client.portal.call(propose)
    assert client.get("/projects/api/board").headers["x-proposed-notes"] == "2"
    first = client.get("/projects/api/notes", params={"status": "proposed"}).json()[0]
    client.patch(f"/notes/{first['id']}", json={"status": "active"})
    assert client.get("/projects/api/board").headers["x-proposed-notes"] == "1"


def test_export_and_import_a_file(client):
    for slug in ("api", "web"):
        client.post("/projects", json={"slug": slug, "repo": f"acme/{slug}"})
    uv = _note(client, "api", "Use uv", tags=["python"])
    _note(client, "api", "Small PRs", tags=["process"])
    old = _note(client, "api", "Old way", tags=["python"])
    client.patch(f"/notes/{old['id']}", json={"status": "archived"})

    r = client.get("/projects/api/notes/export")
    assert r.status_code == 200 and 'filename="api-notes.json"' in r.headers["content-disposition"]
    file = r.json()
    assert (file["format"], file["project"]) == ("rubens-notes/1", "api")
    assert [(n["id"], n["title"]) for n in file["notes"]][0] == (uv["id"], "Use uv")
    assert len(file["notes"]) == 2  # active only
    picked = client.get("/projects/api/notes/export", params={"tags": "PYTHON"}).json()["notes"]
    assert [n["title"] for n in picked] == ["Use uv"]
    assert client.get("/projects/api/notes/export", params={"note_ids": old["id"]}).status_code == 404
    md = client.get("/projects/api/notes/export", params={"format": "markdown"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert "# Project memory: api" in md.text and "## Use uv\n\n_Tags: python_" in md.text

    # Imported notes wait for a person; importing the same file again, or back home, adds nothing.
    res = client.post("/projects/web/notes/import", json=file).json()
    assert res["skipped"] == 0
    copy = next(n for n in res["copied"] if n["title"] == "Use uv")
    assert (copy["status"], copy["created_by"], copy["origin_note_id"]) == ("proposed", "import:api", uv["id"])
    assert client.post("/projects/web/notes/import", json=file).json() == {"copied": [], "skipped": 2}
    assert client.post("/projects/api/notes/import", json=file).json() == {"copied": [], "skipped": 2}
    # A note approved after import exports with its first origin, so it is not imported back either.
    client.patch(f"/notes/{copy['id']}", json={"status": "active"})
    again = client.get("/projects/web/notes/export").json()
    assert again["notes"][0]["id"] == uv["id"]
    assert client.post("/projects/api/notes/import", json=again).json() == {"copied": [], "skipped": 1}

    # A file from anywhere: clipped and normalized as agent proposals are, refused when malformed.
    raw = {"format": "rubens-notes/1", "project": "elsewhere", "notes": [
        {"id": "x1", "title": "  T" * 100, "content": "c" * 30_000, "tags": [" CI ", "ci", ""] + list("abcdefghijk")},
        {"id": "x2", "title": " ", "content": "empty title"},
        {"id": "x1", "title": "duplicate id", "content": "c"},
    ]}
    (note,) = client.post("/projects/web/notes/import", json=raw).json()["copied"]
    assert len(note["title"]) == 200 and len(note["content"]) == memory.IMPORTED_CONTENT_LIMIT
    assert note["tags"][:2] == ["ci", "a"] and len(note["tags"]) == memory.MAX_TAGS
    assert note["created_by"] == "import:elsewhere"
    assert client.post("/projects/web/notes/import", json={**raw, "format": "other/1"}).status_code == 422
    too_many = {**raw, "notes": [{"id": str(i), "title": "t", "content": "c"} for i in range(memory.MAX_IMPORTED + 1)]}
    assert client.post("/projects/web/notes/import", json=too_many).status_code == 422
    assert client.post("/projects/web/notes/import", json={**raw, "notes": [{"id": "y" * 33}]}).status_code == 422


async def test_render_keeps_the_newest_within_the_limit(db, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_LIMIT", 120)
    async with db.session() as s:
        s.add(p := Project(slug="p", repo="o/r"))
        await s.flush()
        assert await memory.render(s, p.id) == ""
        now = utcnow()
        for i in range(3):
            at = now + timedelta(seconds=i)
            s.add(Note(project_id=p.id, title=f"n{i}", content="x" * 40, created_by="user", updated_at=at))
        await s.commit()
        text = await memory.render(s, p.id)
    assert '<note title="n2">' in text and '<note title="n0">' not in text
    assert "older notes left out" in text
