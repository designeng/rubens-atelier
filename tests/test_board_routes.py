"""The board API is split into routers (app/board_api/); the set of routes must not change."""

from app.api import app

BOARD_ROUTES = {
    ("DELETE", "/items/{item_id}"),
    ("DELETE", "/scheduler/limits"),
    ("DELETE", "/workspaces/{workspace_id}"),
    ("GET", "/github/app"),
    ("GET", "/github/installations/{installation_id}/repositories"),
    ("GET", "/items/{item_id}"),
    ("GET", "/items/{item_id}/exec/{exec_id}"),
    ("GET", "/items/{item_id}/git"),
    ("GET", "/models"),
    ("GET", "/projects"),
    ("GET", "/projects/{slug}/board"),
    ("GET", "/projects/{slug}/inbox"),
    ("GET", "/scheduler"),
    ("GET", "/workspaces"),
    ("PATCH", "/items/{item_id}"),
    ("PATCH", "/projects/{slug}"),
    ("PATCH", "/scheduler/limits"),
    ("POST", "/items/{item_id}/cancel"),
    ("POST", "/items/{item_id}/exec"),
    ("POST", "/items/{item_id}/open-pr"),
    ("POST", "/items/{item_id}/reopen"),
    ("POST", "/items/{item_id}/run-now"),
    ("POST", "/items/{item_id}/start"),
    ("POST", "/projects"),
    ("POST", "/projects/{slug}/import-issues"),
    ("POST", "/projects/{slug}/items"),
    ("POST", "/requests/{request_id}/answer"),
    ("POST", "/scheduler/pause"),
    ("POST", "/scheduler/resume"),
    ("POST", "/workspaces/prune"),
}


def test_board_routes_are_unchanged():
    documented = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}
    assert BOARD_ROUTES <= documented


def test_prune_is_matched_before_the_workspace_id():
    paths = [p for p in app.openapi()["paths"] if p.startswith("/workspaces/")]
    assert paths.index("/workspaces/prune") < paths.index("/workspaces/{workspace_id}")
