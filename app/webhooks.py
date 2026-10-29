"""GitHub webhook: re-evaluate check states when GitHub reports something, instead of polling.

Events that can change a verdict (a check run, check suite or commit status finishing, a PR
being closed) are matched to the items waiting on that commit or PR, and `poll_checks` is
enqueued for just those. The checks themselves still ask GitHub for the full picture on the
worker, so a forged or replayed delivery can at most trigger a re-check.

Deliveries are authenticated by the `X-Hub-Signature-256` HMAC of the raw body with
`github_webhook_secret`, not by the API token (GitHub can't send one). No secret = no endpoint.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from .checks import watched_states
from .config import settings
from .db import Project, WorkItem

router = APIRouter()


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def relevant(event: str, payload: dict) -> tuple[str | None, int | None]:
    """(head sha, PR number) an event that may settle a check is about; (None, None) otherwise."""
    action, sha, number = payload.get("action"), None, None
    if event in ("check_run", "check_suite") and action == "completed":
        sha = (payload.get(event) or {}).get("head_sha")
    elif event == "status" and payload.get("state") not in (None, "pending"):
        sha = payload.get("sha")
    elif event == "pull_request" and action == "closed":
        number = (payload.get("pull_request") or {}).get("number")
    return (sha if isinstance(sha, str) and sha else None), (number if isinstance(number, int) else None)


@router.post("/github/webhook")
async def github_webhook(request: Request) -> dict:
    if not settings.github_webhook_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhooks are not configured")
    body = await request.body()
    sent = request.headers.get("x-hub-signature-256", "")
    if not hmac.compare_digest(sent.encode(), signature(settings.github_webhook_secret, body).encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from None

    if not isinstance(payload, dict):
        return {"items": []}
    repo = (payload.get("repository") or {}).get("full_name")
    sha, number = relevant(request.headers.get("x-github-event", ""), payload)
    if not isinstance(repo, str) or sha is None and number is None:
        return {"items": []}

    engine = request.app.state.engine
    match = WorkItem.head_sha == sha if sha else WorkItem.pr_url.like(f"%/pull/{number}")
    async with engine.db.session() as s:
        ids = list(await s.scalars(
            select(WorkItem.id)
            .join(Project, Project.id == WorkItem.project_id)
            .where(func.lower(Project.repo) == repo.lower(), WorkItem.state.in_(watched_states(engine)), match)
        ))
    if ids:
        await request.app.state.checks_enqueue(ids)
    return {"items": ids}
