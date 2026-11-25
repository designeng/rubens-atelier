"""Compares the files a plan names with the files the branch changes. Plain string matching: the
result is a short note for the reviewer, who judges whether a deviation is justified."""

from fnmatch import fnmatchcase
from typing import Any

MAX_LISTED = 30  # files named per list in the note
MAX_PLANNED = 200
MAX_PATH = 300

FILES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Repo-relative paths the plan adds, changes or deletes, tests and docs included. "
        "A glob or `dir/` covers several. Empty if there is no plan yet."
    ),
}


def clean(files: Any) -> list[str] | None:
    """The planner's list, normalized; None if it gave none."""
    if not isinstance(files, list):
        return None
    paths = (f.strip().removeprefix("./")[:MAX_PATH] for f in files if isinstance(f, str))
    return list(dict.fromkeys(p for p in paths if p))[:MAX_PLANNED] or None


def _matches(path: str, pattern: str) -> bool:
    # Equality first: `[id].vue` is a file name as well as a glob.
    return path == pattern or path.startswith(pattern.rstrip("/") + "/") or fnmatchcase(path, pattern)


def _listed(files: list[str]) -> str:
    more = len(files) - MAX_LISTED
    return "\n".join(f"- {f}" for f in files[:MAX_LISTED]) + (f"\n- … and {more} more" if more > 0 else "")


def note(planned: list[str] | None, changed: list[str]) -> str:
    """Empty when the change matches the plan or the plan named no files."""
    if not planned:
        return ""
    outside = [c for c in changed if not any(_matches(c, p) for p in planned)]
    untouched = [p for p in planned if not any(_matches(c, p) for c in changed)]
    parts = []
    if outside:
        parts.append(f"Changed but not in the plan:\n{_listed(outside)}")
    if untouched:
        parts.append(f"In the plan but not changed:\n{_listed(untouched)}")
    return "\n".join(parts)
