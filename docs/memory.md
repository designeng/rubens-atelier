# Project memory

Artifacts carry knowledge between the steps of one story; notes carry it between stories. A note is a lesson for the project's agents: a convention, a pitfall, an approach that failed, a CI quirk. Each project keeps its own (projects differ in language and requirements). Active notes go into every fresh agent session's prompt, newest first, up to 8000 characters; a resumed session has read them already. Agents with a structured answer (planner, reviewer) may propose up to 3 notes per answer; those stay `proposed` and never reach a prompt until a person approves them, because a story's text can come from outside. A title the project already has is not proposed twice. A fragment of one project's memory (picked notes and/or every note with a tag) can be copied into another; copies remember their origin, so copying again skips what is there. To carry memory to another factory or keep it in git, export active notes as a JSON file (`rubens-notes/1`) and import it elsewhere; imported notes arrive `proposed`, since the file may come from anywhere, and are clipped and tag-normalized like agent proposals (at most 500 notes per file). Re-importing skips what is there, by the same origin logic. A Markdown export is for reading. On the board: project menu → **Project memory…**; the header shows a chip when proposed notes wait for review.

| Method | Path | |
|---|---|---|
| `GET` | `/projects/{slug}/notes?status=&tag=` | Notes, newest first; `status` is `proposed`, `active` or `archived` |
| `POST` | `/projects/{slug}/notes` | `{title, content, tags?}`: a person's note, active at once |
| `PATCH` | `/notes/{id}` | `{title?, content?, tags?, status?}`: `status: active` approves a proposed note, `archived` retires one |
| `DELETE` | `/notes/{id}` | Reject a proposed note or drop one for good |
| `POST` | `/projects/{slug}/notes/copy` | `{from_project, note_ids?, tags?}`: copy those active notes here as active; returns `{copied, skipped}` |
| `GET` | `/projects/{slug}/notes/export?note_ids=&tags=&format=` | Active notes as a file: the listed ones plus those with a tag, or all; `format` is `json` (`{format: "rubens-notes/1", project, exported_at, notes: [{id, title, content, tags}]}`) or `markdown` |
| `POST` | `/projects/{slug}/notes/import` | An exported JSON file: adds its notes as `proposed`; returns `{copied, skipped}` |
