"""GitHub App auth: app JWT -> installation access tokens scoped to a single repository.

An installation token lives one hour and is minted for exactly the project's repo, so a
leaked token is worth far less than a PAT. Tokens are cached per process and renewed a
few minutes before they expire.
"""

import time
from datetime import datetime
from typing import Any

import httpx
import jwt

from .config import settings

REFRESH_MARGIN = 300  # seconds before expiry at which a cached token is replaced


class AppNotConfigured(RuntimeError):
    pass


def private_key() -> str:
    key = settings.github_app_private_key.replace("\\n", "\n")
    if not key and settings.github_app_private_key_file:
        key = settings.github_app_private_key_file.read_text()
    return key


def configured() -> bool:
    return bool(settings.github_app_id and private_key())


def install_url() -> str | None:
    slug = settings.github_app_slug
    return f"https://github.com/apps/{slug}/installations/new" if slug else None


def app_jwt() -> str:
    if not configured():
        raise AppNotConfigured("HARNESS_GITHUB_APP_ID and HARNESS_GITHUB_APP_PRIVATE_KEY are not set")
    now = int(time.time())
    # iat is backdated to tolerate clock drift; GitHub caps the lifetime at 10 minutes.
    claims = {"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id}
    return jwt.encode(claims, private_key(), algorithm="RS256")


_cache: dict[tuple[int, str | None], tuple[str, float]] = {}


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.github_api_url,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30,
        transport=transport,
    )


async def installation_token(
    installation_id: int, repo: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """A token for the installation, restricted to `repo` ("owner/name") when given."""
    key = (installation_id, repo)
    cached = _cache.get(key)
    if cached and cached[1] - time.time() > REFRESH_MARGIN:
        return cached[0]
    body = {"repositories": [repo.split("/", 1)[1]]} if repo else {}
    async with _client(transport) as http:
        resp = await http.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt()}"},
            json=body,
        )
        resp.raise_for_status()
    data = resp.json()
    expires = datetime.fromisoformat(data["expires_at"]).timestamp()
    _cache[key] = (data["token"], expires)
    return data["token"]


async def list_installations(*, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    async with _client(transport) as http:
        resp = await http.get(
            "/app/installations", headers={"Authorization": f"Bearer {app_jwt()}"}, params={"per_page": 100}
        )
        resp.raise_for_status()
    return [
        {
            "id": i["id"],
            "account": i["account"]["login"],
            "repository_selection": i["repository_selection"],
            "manage_url": i.get("html_url"),
            "suspended": bool(i.get("suspended_at")),
        }
        for i in resp.json()
    ]


async def list_repositories(
    installation_id: int, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    """Repositories the installation was granted (the user's choice on github.com)."""
    token = await installation_token(installation_id, transport=transport)
    repos: list[dict[str, Any]] = []
    async with _client(transport) as http:
        for page in range(1, 11):
            resp = await http.get(
                "/installation/repositories",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()["repositories"]
            repos += [
                {"full_name": r["full_name"], "default_branch": r["default_branch"], "private": r["private"]}
                for r in batch
            ]
            if len(batch) < 100:
                break
    return sorted(repos, key=lambda r: r["full_name"])
