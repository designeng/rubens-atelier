import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import github_app
from app.config import settings
from app.github import GitHub


@pytest.fixture
def app_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_slug", "my-factory")
    monkeypatch.setattr(settings, "github_app_private_key", pem)
    monkeypatch.setattr(github_app, "_cache", {})
    return key.public_key()


def fake_github(app_key, expires_in=3600):
    """Mints tokens like GitHub does and records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        auth = request.headers["authorization"].removeprefix("Bearer ")
        if request.url.path.startswith("/app/"):
            claims = jwt.decode(auth, app_key, algorithms=["RS256"], options={"verify_iat": False})
            assert claims["iss"] == "123" and claims["exp"] - time.time() <= 600
        if request.url.path == "/app/installations/7/access_tokens":
            exp = (datetime.now(UTC) + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return httpx.Response(201, json={"token": f"tok-{len(seen)}", "expires_at": exp})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [
                {"full_name": "acme/b", "default_branch": "main", "private": True},
                {"full_name": "acme/a", "default_branch": "dev", "private": False},
            ]})
        if request.url.path == "/app/installations":
            return httpx.Response(200, json=[
                {"id": 7, "account": {"login": "acme"}, "repository_selection": "selected", "html_url": "u"}
            ])
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


async def test_token_is_scoped_to_the_repo_and_cached(app_key):
    transport, seen = fake_github(app_key)
    t1 = await github_app.installation_token(7, "acme/app", transport=transport)
    t2 = await github_app.installation_token(7, "acme/app", transport=transport)
    assert t1 == t2 and len(seen) == 1
    assert json.loads(seen[0].content) == {"repositories": ["app"]}


async def test_token_is_renewed_before_it_expires(app_key):
    transport, seen = fake_github(app_key, expires_in=100)  # inside the refresh margin
    t1 = await github_app.installation_token(7, "acme/app", transport=transport)
    t2 = await github_app.installation_token(7, "acme/app", transport=transport)
    assert t1 != t2 and len(seen) == 2


async def test_lists_installations_and_sorted_repos(app_key):
    transport, _ = fake_github(app_key)
    (inst,) = await github_app.list_installations(transport=transport)
    assert inst["account"] == "acme" and inst["repository_selection"] == "selected"
    repos = await github_app.list_repositories(7, transport=transport)
    assert [r["full_name"] for r in repos] == ["acme/a", "acme/b"]


async def test_github_client_uses_installation_token(app_key, monkeypatch):
    calls = []

    async def fake_token(installation_id, repo=None, **_):
        calls.append((installation_id, repo))
        return f"tok-{len(calls)}"

    monkeypatch.setattr(github_app, "installation_token", fake_token)
    gh = GitHub(installation_id=7, repo="acme/app")
    gh.http._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"auth": r.headers["authorization"]}))
    assert (await gh.get_repo("acme/app"))["auth"] == "Bearer tok-1"
    assert (await gh.get_repo("acme/app"))["auth"] == "Bearer tok-2"  # resolved per request
    assert await gh.git_token() == "tok-3" and calls[0] == (7, "acme/app")
    await gh.aclose()


def test_installation_without_app_config_fails_fast(monkeypatch):
    monkeypatch.setattr(settings, "github_app_id", "")
    with pytest.raises(RuntimeError, match="not configured"):
        GitHub(installation_id=7, repo="acme/app")


async def test_list_issues_pages_and_skips_prs():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append((page, request.url.params["state"], request.url.params["per_page"]))
        if page == 1:
            return httpx.Response(200, json=[{"number": n} for n in range(1, 100)] + [{"number": 100, "pull_request": {}}])
        return httpx.Response(200, json=[{"number": 101}, {"number": 102, "pull_request": {}}])

    def mock(gh, handler):  # a fresh client, so proxy mounts from the environment don't bypass the mock
        gh.http = httpx.AsyncClient(base_url="https://api.github.test", transport=httpx.MockTransport(handler))

    gh = GitHub(token="t")
    await gh.aclose()
    mock(gh, handler)
    issues = await gh.list_issues("acme/app")
    assert [i["number"] for i in issues] == [*range(1, 100), 101]
    assert pages == [(1, "open", "100"), (2, "open", "100")]
    await gh.aclose()

    # Always-full pages stop at max_pages.
    mock(gh, lambda r: httpx.Response(200, json=[{"number": 1}] * 100))
    assert len(await gh.list_issues("acme/app", max_pages=3)) == 300
    await gh.aclose()


def test_api_status_and_project_validation(client, app_key, monkeypatch):
    transport, _ = fake_github(app_key)
    real_installations, real_repos = github_app.list_installations, github_app.list_repositories
    monkeypatch.setattr(github_app, "list_installations", lambda: real_installations(transport=transport))
    monkeypatch.setattr(github_app, "list_repositories", lambda i: real_repos(i, transport=transport))
    monkeypatch.setattr(github_app, "installation_token", _token_via(transport))

    status = client.get("/github/app").json()
    assert status["configured"] and status["install_url"] == "https://github.com/apps/my-factory/installations/new"
    assert status["installations"][0]["id"] == 7
    assert [r["full_name"] for r in client.get("/github/installations/7/repositories").json()] == ["acme/a", "acme/b"]

    ok = client.post("/projects", json={"slug": "a", "repo": "acme/a", "installation_id": 7})
    assert ok.status_code == 201 and ok.json()["installation_id"] == 7
    denied = client.post("/projects", json={"slug": "c", "repo": "acme/secret", "installation_id": 7})
    assert denied.status_code == 422 and "not granted" in denied.json()["detail"]


def _token_via(transport):
    real = github_app.installation_token

    async def token(installation_id, repo=None, **_):
        return await real(installation_id, repo, transport=transport)

    return token


def test_api_reports_unconfigured_app(client, monkeypatch):
    monkeypatch.setattr(settings, "github_app_id", "")
    assert client.get("/github/app").json() == {"configured": False, "install_url": None, "installations": []}
