import { describe, expect, it, vi } from "vitest";
import { ApiError, createClient, parseSse } from "../api/client";

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

describe("parseSse", () => {
  it("splits complete messages and keeps the tail", () => {
    const { messages, rest } = parseSse('id: 1-0\r\nevent: text\r\ndata: {"a":1}\r\n\r\n: ping\r\n\r\nid: 2-0\r\nda');
    expect(messages).toEqual([{ id: "1-0", event: "text", data: '{"a":1}' }]);
    expect(rest).toBe("id: 2-0\nda");
  });

  it("does not end a message on a CRLF split across chunks", () => {
    const first = parseSse("event: text\r\ndata: x\r");
    expect(first.messages).toEqual([]);
    const second = parseSse(first.rest + "\n\r\n");
    expect(second.messages).toEqual([{ id: "", event: "text", data: "x" }]);
  });
});

describe("streamTask", () => {
  it("streams events from the cursor with the token", async () => {
    const chunks = ['id: 1-0\r\nevent: status\r\ndata: {"status": "run', 'ning"}\r\n\r\n', ": ping\r\n\r\n"];
    const body = new ReadableStream({
      start(c) {
        for (const ch of chunks) c.enqueue(new TextEncoder().encode(ch));
        c.close();
      },
    });
    const f = fakeFetch(() => new Response(body, { headers: { "content-type": "text/event-stream" } }));
    const api = createClient(() => "t", f.impl);
    const got: unknown[] = [];
    await api.streamTask("a/b", "5-0", (e) => got.push(e));
    expect(f.calls[0].url).toBe("/tasks/a%2Fb/stream?after=5-0");
    expect((f.calls[0].init.headers as Record<string, string>).authorization).toBe("Bearer t");
    expect(got).toEqual([{ id: "1-0", type: "status", data: { status: "running" } }]);
  });

  it("throws ApiError for a missing task", async () => {
    const f = fakeFetch(() => json({ detail: "task not found" }, 404, "Not Found"));
    const api = createClient(() => "t", f.impl);
    await expect(api.streamTask("x", "0", () => {})).rejects.toMatchObject({ status: 404, message: "task not found" });
  });
});
