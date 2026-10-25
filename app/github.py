"""Minimal async GitHub REST client for the issue workflow."""

from typing import Any

import httpx

from .config import settings


class GitHub:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        token = token or settings.github_token
        if not token:
            raise RuntimeError("HARNESS_GITHUB_TOKEN is not set")
        self.token = token
        self.http = httpx.AsyncClient(
            base_url=base_url or settings.github_api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

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
