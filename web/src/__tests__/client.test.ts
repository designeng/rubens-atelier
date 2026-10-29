import { describe, expect, it, vi } from "vitest";
import { ApiError, createClient } from "../api/client";

type Call = { url: string; init: RequestInit };

function fakeFetch(respond: () => Response) {
  const calls: Call[] = [];
  const impl = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return respond();
  }) as unknown as typeof fetch;
  return { calls, impl };
}

const json = (data: unknown, status = 200, statusText = "OK") =>
  new Response(JSON.stringify(data), { status, statusText, headers: { "content-type": "application/json" } });

describe("createClient", () => {
  it("sends the current token as a bearer header", async () => {
    let token = "first";
    const f = fakeFetch(() => json([]));
    const api = createClient(() => token, f.impl);
    await api.listProjects();
    token = "second";
    await api.getScheduler();
    const auth = f.calls.map((c) => (c.init.headers as Record<string, string>).authorization);
    expect(auth).toEqual(["Bearer first", "Bearer second"]);
    expect(f.calls[0].init.method).toBe("GET");
    expect(f.calls[0].init.body).toBeUndefined();
  });

  it("asks for the git report, with the diff on request", async () => {
    const f = fakeFetch(() => json({}));
    const api = createClient(() => "t", f.impl);
    await api.getItemGit("a/b");
    await api.getItemGit("a/b", true);
    expect(f.calls.map((c) => c.url)).toEqual(["/items/a%2Fb/git", "/items/a%2Fb/git?diff=true"]);
  });

  it("patches an item", async () => {
    const f = fakeFetch(() => json({}));
    const api = createClient(() => "t", f.impl);
    await api.updateItem("a/b", { priority: 3, model: null });
    expect([f.calls[0].url, f.calls[0].init.method, JSON.parse(f.calls[0].init.body as string)]).toEqual([
      "/items/a%2Fb",
      "PATCH",
      { priority: 3, model: null },
    ]);
  });

  it("patches scheduler limits", async () => {
    const f = fakeFetch(() => json({}));
    const api = createClient(() => "t", f.impl);
    await api.setSchedulerLimits({ max_agents: 2 });
    expect([f.calls[0].url, f.calls[0].init.method, JSON.parse(f.calls[0].init.body as string)]).toEqual([
      "/scheduler/limits",
      "PATCH",
      { max_agents: 2 },
    ]);
  });

  it("reopens a story with a POST", async () => {
    const f = fakeFetch(() => json({}));
    await createClient(() => "t", f.impl).reopenItem("i1");
    expect([f.calls[0].url, f.calls[0].init.method]).toEqual(["/items/i1/reopen", "POST"]);
  });

  it("posts JSON bodies", async () => {
    const f = fakeFetch(() => json({ advanced: true }));
    const api = createClient(() => "t", f.impl);
    await expect(api.answerRequest("r1", "approve", "looks good")).resolves.toEqual({ advanced: true });
    await api.createItem("app", { title: "x", body: "y", start: true });
    expect(f.calls.map((c) => [c.url, c.init.method, JSON.parse(c.init.body as string)])).toEqual([
      ["/requests/r1/answer", "POST", { option: "approve", comment: "looks good" }],
      ["/projects/app/items", "POST", { title: "x", body: "y", start: true }],
    ]);
    expect((f.calls[0].init.headers as Record<string, string>)["content-type"]).toBe("application/json");
  });

  it("encodes path segments", async () => {
    const f = fakeFetch(() => json({}));
    const api = createClient(() => "t", f.impl);
    await api.getBoard("a/b?c");
    await api.getItem("../x");
    await api.installationRepositories(42);
    await api.updateProject("my proj", { verify_command: null });
    await api.importIssues("a/b");
    expect(f.calls.map((c) => c.url)).toEqual([
      "/projects/a%2Fb%3Fc/board",
      "/items/..%2Fx",
      "/github/installations/42/repositories",
      "/projects/my%20proj",
      "/projects/a%2Fb/import-issues",
    ]);
    expect(f.calls[3].init.method).toBe("PATCH");
    expect(JSON.parse(f.calls[3].init.body as string)).toEqual({ verify_command: null });
    expect([f.calls[4].init.method, f.calls[4].init.body]).toEqual(["POST", undefined]);
  });

  it("raises ApiError with the server's detail", async () => {
    const api = (r: () => Response) => createClient(() => "t", fakeFetch(r).impl);
    const err = await api(() => json({ detail: "invalid or missing bearer token" }, 401))
      .listProjects()
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ message: "invalid or missing bearer token", status: 401 });

    const detail = [{ loc: ["body", "slug"], msg: "bad" }];
    await expect(api(() => json({ detail }, 422)).listProjects()).rejects.toThrow(JSON.stringify(detail));

    const notJson = () => new Response("<html>oops</html>", { status: 502, statusText: "Bad Gateway" });
    await expect(api(notJson).listProjects()).rejects.toThrow("Bad Gateway");
  });

  it("calls onOk only after a successful response", async () => {
    const onOk = vi.fn();
    let ok = false;
    const f = fakeFetch(() => (ok ? json([]) : json({ detail: "no" }, 500)));
    const api = createClient(() => "t", f.impl, onOk);
    await api.listProjects().catch(() => {});
    expect(onOk).not.toHaveBeenCalled();
    ok = true;
    await api.listProjects();
    expect(onOk).toHaveBeenCalledOnce();
  });
});
