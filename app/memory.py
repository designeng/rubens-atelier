"""Project memory: lessons that outlive a story, kept per project and put into later prompts.

Artifacts carry knowledge between the steps of one item; notes carry it between items. A note
is a convention, a pitfall, an approach that failed, a CI quirk: whatever the next agent in this
project would otherwise rediscover. Projects differ (language, tooling, requirements), so each
keeps its own notes, and a fragment of one project's memory (picked notes, or every note with
a tag) can be copied into another, or exported to a file and imported on another factory.

Agents propose notes through the `notes` field of their structured answer; those wait as
`proposed` until a person approves them (`active`) or deletes them. Only active notes reach
prompts: a story's text can come from outside, and a lesson drawn from it must not steer every
later story without a person reading it first.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .board_api import Db, Orm, _project
from .db import Note, Project, utcnow

Status = Literal["proposed", "active", "archived"]

MEMORY_LIMIT = 8_000  # characters of notes in one prompt; the newest go first
MAX_PROPOSED = 3  # notes one agent answer may propose
MAX_TAGS = 10
PROPOSED_CONTENT_LIMIT = 4_000
EXPORT_FORMAT = "rubens-notes/1"
MAX_IMPORTED = 500  # notes in one imported file
IMPORTED_CONTENT_LIMIT = 20_000

MEMORY = """
Project memory: notes the people of this project keep for agents (conventions, pitfalls,
approaches that failed). Follow them unless the task says otherwise.

<project_memory>
$notes</project_memory>
"""

router = APIRouter()

Tag = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=40)]


def normalize_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t.strip().lower()[:40] for t in tags if t.strip()))[:MAX_TAGS]


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def _dedupe(cls, tags: list[str]) -> list[str]:
        return list(dict.fromkeys(tags))


class NotePatch(BaseModel):
    """Fields left out stay; `status: active` approves a proposed note, `archived` retires one."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=None, min_length=1, max_length=200)
    content: str = Field(default=None, min_length=1)
    tags: list[Tag] = Field(default=None, max_length=MAX_TAGS)
    status: Status = Field(default=None)

    @field_validator("tags")
    @classmethod
    def _dedupe(cls, tags: list[str] | None) -> list[str] | None:
        return tags if tags is None else list(dict.fromkeys(tags))


class NoteOut(Orm):
    id: str
    project_id: str
    title: str
    content: str
    tags: list[str]
    status: str
    created_by: str
    source_item_id: str | None
    origin_note_id: str | None
    created_at: datetime
    updated_at: datetime


class CopyIn(BaseModel):
    """A fragment of another project's memory: the listed notes plus every note with one of `tags`."""

    from_project: str
    note_ids: list[str] = Field(default_factory=list, max_length=200)
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)

    @model_validator(mode="after")
    def _some(self) -> "CopyIn":
        if not self.note_ids and not self.tags:
            raise ValueError("pick the notes to copy: note_ids, tags or both")
        return self


class CopyResult(BaseModel):
    copied: list[NoteOut]
    skipped: int  # already in the target project (copied before, or copied from it)


class FileNote(BaseModel):
    """A note in an export file; `id` is its first origin, so copies of one note share it. The
    raw fields are only bounded here: import clips and normalizes them as propose() does."""

    id: str = Field(min_length=1, max_length=32)
    title: str = Field(max_length=10_000)
    content: str = Field(max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=100)


class NotesFile(BaseModel):
    """What export writes and import reads: active notes of `project`, to keep in git or carry
    to another factory."""

    format: Literal["rubens-notes/1"] = EXPORT_FORMAT
    project: str = Field(min_length=1, max_length=200)
    exported_at: datetime | None = None
    notes: list[FileNote] = Field(max_length=MAX_IMPORTED)


@router.get("/projects/{slug}/notes")
async def list_notes(slug: str, db: Db, status: Status | None = None, tag: str | None = None) -> list[NoteOut]:
    """A project's notes, newest first: proposed ones waiting for a person, active ones agents
    read, archived ones kept for the record."""
    project = await _project(db, slug)
    async with db.session() as s:
        q = select(Note).where(Note.project_id == project.id).order_by(Note.updated_at.desc())
        if status:
            q = q.where(Note.status == status)
        notes = list(await s.scalars(q))
    if tag:
        notes = [n for n in notes if tag.strip().lower() in n.tags]
    return [NoteOut.model_validate(n) for n in notes]


@router.post("/projects/{slug}/notes", status_code=status.HTTP_201_CREATED)
async def create_note(slug: str, body: NoteIn, db: Db) -> NoteOut:
    """A note written by a person is active at once: agents read it from their next fresh session."""
    project = await _project(db, slug)
    async with db.session() as s:
        note = Note(project_id=project.id, created_by="user", status="active", **body.model_dump())
        s.add(note)
        await s.commit()
        return NoteOut.model_validate(note)


@router.patch("/notes/{note_id}")
async def update_note(note_id: str, body: NotePatch, db: Db) -> NoteOut:
    async with db.session() as s:
        note = await _get(s, note_id)
        fields = body.model_dump(exclude_unset=True)
        if any(v is None for v in fields.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "fields cannot be null")
        for field, value in fields.items():
            setattr(note, field, value)
        note.updated_at = utcnow()
        await s.commit()
        return NoteOut.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, db: Db) -> Response:
    """Reject a proposed note, or drop one for good (archive it instead to keep the record)."""
    async with db.session() as s:
        await s.delete(await _get(s, note_id))
        await s.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{slug}/notes/copy")
async def copy_notes(slug: str, body: CopyIn, db: Db) -> CopyResult:
    """Copy active notes of `from_project` into this project, as active notes: a person picked
    them. Copies remember their origin, so copying the same fragment again skips what is there."""
    target = await _project(db, slug)
    source = await _project(db, body.from_project)
    if source.id == target.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "copy from another project")
    async with db.session() as s:
        picked = await _pick(s, source, body.note_ids, body.tags)
        roots = {n.origin_note_id or n.id: n for n in picked}
        return await _add_copies(s, target.id, roots, "active", f"copy:{source.slug}")


@router.get("/projects/{slug}/notes/export", response_model=None)
async def export_notes(
    slug: str,
    db: Db,
    response: Response,
    tags: Annotated[list[str] | None, Query()] = None,
    note_ids: Annotated[list[str] | None, Query()] = None,
    fmt: Annotated[Literal["json", "markdown"], Query(alias="format")] = "json",
) -> NotesFile | Response:
    """Active notes as a file: the listed ones plus every one with one of `tags`, or all of them
    when neither is given. JSON can be imported back (here or on another factory); `markdown`
    is for people to read."""
    project = await _project(db, slug)
    async with db.session() as s:
        picked = await _pick(s, project, note_ids or [], normalize_tags(tags or []))
    file = NotesFile(
        project=project.slug,
        exported_at=utcnow(),
        notes=[FileNote(id=n.origin_note_id or n.id, title=n.title, content=n.content, tags=n.tags) for n in picked],
    )
    name = f"{project.slug}-notes"
    if fmt == "markdown":
        return Response(
            _markdown(file),
            media_type="text/markdown; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{name}.md"'},
        )
    response.headers["content-disposition"] = f'attachment; filename="{name}.json"'
    return file


@router.post("/projects/{slug}/notes/import")
async def import_notes(slug: str, body: NotesFile, db: Db) -> CopyResult:
    """Add the notes of an export file as `proposed`: the file may come from anywhere, so a person
    reads each one before agents do. Imported notes remember their id in the file as their
    origin, so importing the same file again (or one with copies of them) skips what is there."""
    project = await _project(db, slug)
    roots: dict[str, FileNote] = {}
    for n in body.notes:
        title, content = n.title.strip()[:200], n.content.strip()[:IMPORTED_CONTENT_LIMIT]
        if title and content and n.id not in roots:
            roots[n.id] = FileNote(id=n.id, title=title, content=content, tags=normalize_tags(n.tags))
    async with db.session() as s:
        return await _add_copies(s, project.id, roots, "proposed", f"import:{body.project.strip()[:90]}")


async def _pick(s: AsyncSession, project: Project, note_ids: list[str], tags: list[str]) -> list[Note]:
    """The project's active notes that are listed or carry one of `tags`, oldest first; all of
    them when nothing is asked for. A listed id that is not an active note there is a 404."""
    active = list(
        await s.scalars(
            select(Note).where(Note.project_id == project.id, Note.status == "active").order_by(Note.created_at)
        )
    )
    if missing := set(note_ids) - {n.id for n in active}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no active notes {sorted(missing)} in project {project.slug}")
    if not note_ids and not tags:
        return active
    return [n for n in active if n.id in note_ids or set(n.tags) & set(tags)]


async def _add_copies(
    s: AsyncSession, project_id: str, roots: Mapping[str, Note | FileNote], status: Status, created_by: str
) -> CopyResult:
    """Add copies of `roots` (first origin id → the note) to the project, skipping origins it has
    already: the original itself, or an earlier copy in any status. Commits."""
    rows = (
        await s.execute(
            select(Note.id, Note.origin_note_id)
            .where(Note.project_id == project_id)
            .where(or_(Note.id.in_(roots), Note.origin_note_id.in_(roots)))
        )
    ).all()
    present = {r for row in rows for r in row if r}
    copies = [
        Note(
            project_id=project_id,
            title=n.title,
            content=n.content,
            tags=list(n.tags),
            status=status,
            created_by=created_by,
            origin_note_id=root,
        )
        for root, n in roots.items()
        if root not in present
    ]
    s.add_all(copies)
    await s.commit()
    return CopyResult(copied=[NoteOut.model_validate(n) for n in copies], skipped=len(roots) - len(copies))


def _markdown(file: NotesFile) -> str:
    stamp = file.exported_at.strftime("%Y-%m-%d") if file.exported_at else ""
    parts = [f"# Project memory: {file.project}\n\n{len(file.notes)} active notes, exported {stamp}.\n"]
    for n in file.notes:
        tags = f"\n_Tags: {', '.join(n.tags)}_\n" if n.tags else ""
        parts.append(f"\n## {n.title}\n{tags}\n{n.content.strip()}\n")
    return "".join(parts)


async def _get(s: AsyncSession, note_id: str) -> Note:
    note = await s.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    return note


async def render(s: AsyncSession, project_id: str) -> str:
    """The project's active notes as a prompt section, newest first within MEMORY_LIMIT; empty
    when there are none."""
    notes = await s.scalars(
        select(Note).where(Note.project_id == project_id, Note.status == "active").order_by(Note.updated_at.desc())
    )
    parts, size, left_out = [], 0, 0
    for n in notes:
        tags = f' tags="{", ".join(n.tags)}"' if n.tags else ""
        part = f'<note title="{n.title}"{tags}>\n{n.content.strip()}\n</note>\n'
        if size + len(part) > MEMORY_LIMIT:
            left_out += 1
            continue
        parts.append(part)
        size += len(part)
    if not parts:
        return ""
    if left_out:
        parts.append(f"({left_out} older notes left out for space)\n")
    return MEMORY.replace("$notes", "".join(parts))


async def propose(s: AsyncSession, project_id: str, item_id: str, role: str, notes: Any) -> int:
    """Store what an agent's answer proposed as `proposed` notes for a person to review. The answer
    is the agent's, so it is checked and clipped here; a title the project already has (in any
    status) is skipped, so a reviewer repeating itself every round adds nothing. Not committed."""
    if not isinstance(notes, list):
        return 0
    known = {t.lower() for t in await s.scalars(select(Note.title).where(Note.project_id == project_id))}
    added = 0
    for raw in notes[:MAX_PROPOSED]:
        if not isinstance(raw, dict):
            continue
        title, content = str(raw.get("title") or "").strip()[:200], str(raw.get("content") or "").strip()
        if not title or not content or title.lower() in known:
            continue
        tags = raw.get("tags")
        tags = tags if isinstance(tags, list) else []
        s.add(
            Note(
                project_id=project_id,
                title=title,
                content=content[:PROPOSED_CONTENT_LIMIT],
                tags=normalize_tags([str(t) for t in tags]),
                status="proposed",
                created_by=f"agent:{role}",
                source_item_id=item_id,
            )
        )
        known.add(title.lower())
        added += 1
    return added


NOTES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": MAX_PROPOSED,
    "description": (
        "Usually empty. Lessons worth keeping for future tasks in this project that the repo "
        "does not already say: a convention, a pitfall, an approach that failed and why, a CI "
        "quirk. A person reviews them before other agents see them."
    ),
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "One line naming the lesson."},
            "content": {"type": "string", "description": "Markdown: the lesson and why, in a few lines."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "e.g. python, ci, tests"},
        },
        "required": ["title", "content"],
        "additionalProperties": False,
    },
}
