"""Minimal async GitHub REST client for the issue workflow."""

from typing import Any

import httpx

from . import github_app
from .config import settings


class _Auth(httpx.Auth):
    """Resolves the token per request, so a GitHub App token is renewed mid-task."""

    def __init__(self, gh: "GitHub"):
        self.gh = gh

    async def async_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {await self.gh.git_token()}"
        yield request


class GitHub:
    """Client for one repo's worth of work: a GitHub App installation or the server-wide token."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        *,
        installation_id: int | None = None,
        repo: str | None = None,
    ):
        self._token = token or (None if installation_id else settings.github_token)
        self.installation_id, self.repo = installation_id, repo
        if installation_id:
            if not github_app.configured():
                raise RuntimeError("project uses a GitHub App installation but the app is not configured")
        elif not self._token:
            raise RuntimeError("HARNESS_GITHUB_TOKEN is not set")
        self.http = httpx.AsyncClient(
            base_url=base_url or settings.github_api_url,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            auth=_Auth(self),
            timeout=30,
        )

    @classmethod
    def for_project(cls, project) -> "GitHub":
        return cls(installation_id=project.installation_id, repo=project.repo)

    async def git_token(self) -> str:
        """Current token for API calls and git; installation tokens are minted on demand."""
        if self._token:
            return self._token
        return await github_app.installation_token(self.installation_id, self.repo)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self.http.request(method, path, **kw)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def clone_url(self, repo: str) -> str:
        host = httpx.URL(settings.github_api_url).host.removeprefix("api.")
        return f"https://{host}/{repo}.git"

    async def get_repo(self, repo: str) -> dict[str, Any]:
        return await self._req("GET", f"/repos/{repo}")

    async def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        return await self._req("GET", f"/repos/{repo}/issues/{number}")

    async def get_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self._req("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 50})

    async def list_open_issues(self, repo: str, label: str) -> list[dict[str, Any]]:
        items = await self._req(
            "GET", f"/repos/{repo}/issues", params={"state": "open", "labels": label, "per_page": 50}
        )
        return [i for i in items if "pull_request" not in i]  # the endpoint also returns PRs

    async def list_issues(self, repo: str, state: str = "open", max_pages: int = 10) -> list[dict[str, Any]]:
        """All issues of the repo (PRs left out), up to max_pages pages of 100."""
        issues: list[dict[str, Any]] = []
        for p in range(1, max_pages + 1):
            items = await self._req(
                "GET", f"/repos/{repo}/issues", params={"state": state, "per_page": 100, "page": p}
            )
            issues += [i for i in items if "pull_request" not in i]
            if len(items) < 100:
                break
        return issues

    async def comment(self, repo: str, number: int, body: str) -> None:
        await self._req("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    async def set_labels(
        self, repo: str, number: int, add: list[str] = (), remove: list[str] = ()
    ) -> None:
        for label in remove:
            resp = await self.http.delete(f"/repos/{repo}/issues/{number}/labels/{label}")
            if resp.status_code not in (200, 404):
                resp.raise_for_status()
        if add:
            await self._req("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": list(add)})

    async def find_open_pr(self, repo: str, branch: str) -> dict[str, Any] | None:
        owner = repo.split("/")[0]
        prs = await self._req(
            "GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{branch}", "state": "open"}
        )
        return prs[0] if prs else None

    async def create_pr(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/repos/{repo}/pulls",
            json={"head": head, "base": base, "title": title, "body": body},
        )

    async def get_pr(self, repo: str, number: int) -> dict[str, Any]:
        return await self._req("GET", f"/repos/{repo}/pulls/{number}")

    async def merge_pr(self, repo: str, number: int, *, method: str, sha: str | None = None) -> dict[str, Any]:
        """Merge the PR; with `sha`, only if its head is still that commit."""
        body = {"merge_method": method} | ({"sha": sha} if sha else {})
        return await self._req("PUT", f"/repos/{repo}/pulls/{number}/merge", json=body)

    async def check_runs(self, repo: str, ref: str) -> list[dict[str, Any]]:
        data = await self._req("GET", f"/repos/{repo}/commits/{ref}/check-runs", params={"per_page": 100})
        return data["check_runs"]

    async def commit_status(self, repo: str, ref: str) -> dict[str, Any]:
        """Combined legacy commit statuses (CI systems that don't use the Checks API)."""
        return await self._req("GET", f"/repos/{repo}/commits/{ref}/status")

    async def job_log(self, repo: str, job_id: int) -> str:
        # Redirects to blob storage; httpx drops the Authorization header on the cross-origin hop.
        resp = await self.http.get(f"/repos/{repo}/actions/jobs/{job_id}/logs", follow_redirects=True)
        resp.raise_for_status()
        return resp.text
