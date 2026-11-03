"""The board's single-page app: the HTML shell, the favicon and the built assets."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse

page = APIRouter()


# Built by `make web-build` from web/ (Vue + Vite). Read at request time: the API works without a build.
DIST = Path(__file__).parent.parent / "static" / "dist"

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Factory board</title>
<p>Board is not built: run <code>make web-build</code> (needs Node 22 and pnpm) and reload.</p>
"""


@page.get("/board", include_in_schema=False, response_model=None)
async def board_page() -> FileResponse | HTMLResponse:
    index = DIST / "index.html"
    if not index.is_file():
        return HTMLResponse(NOT_BUILT)
    return FileResponse(index, headers={"cache-control": "no-cache"})


@page.get("/board/favicon.svg", include_in_schema=False, response_model=None)
async def board_favicon() -> FileResponse:
    file = DIST / "favicon.svg"
    if not file.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return FileResponse(file, media_type="image/svg+xml")


@page.get("/board/assets/{path:path}", include_in_schema=False)
async def board_asset(path: str) -> FileResponse:
    root = (DIST / "assets").resolve()
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return FileResponse(file)
