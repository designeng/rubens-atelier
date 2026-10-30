import type {
  Board,
  GithubApp,
  GitSnapshot,
  ImportResult,
  ItemDetail,
  ItemIn,
  ItemOut,
  ProjectIn,
  ProjectOut,
  ItemPatch,
  Models,
  ProjectPatch,
  PruneIn,
  PruneResult,
  Repository,
  SchedulerLimits,
  SchedulerStatus,
  TaskEvent,
  TaskInfo,
  WorkspaceList,
  WorkspacePhase,
  WorkspaceQueued,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const seg = (s: string | number) => encodeURIComponent(String(s));

async function apiError(res: Response): Promise<ApiError> {
  const detail: unknown = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
  const msg = detail || res.statusText;
  return new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg), res.status);
}

export interface SseMessage {
  id: string;
  event: string;
  data: string;
}

// Splits a server-sent events buffer into complete messages; `rest` is the unfinished tail.
// Comments (the server's pings) and messages without data are dropped.
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  // A "\r" at the very end may be the first half of "\r\n": keep it for the next chunk.
  const cut = buffer.endsWith("\r") ? buffer.length - 1 : buffer.length;
  const blocks = buffer.slice(0, cut).replace(/\r\n?/g, "\n").split("\n\n");
  const rest = blocks.pop()! + buffer.slice(cut);
  const messages: SseMessage[] = [];
  for (const block of blocks) {
    const m: SseMessage = { id: "", event: "message", data: "" };
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const field = i < 0 ? line : line.slice(0, i);
      const value = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
      if (field === "data") data.push(value);
      else if (field === "id" || field === "event") m[field] = value;
    }
    if (data.length) messages.push({ ...m, data: data.join("\n") });
  }
  return { messages, rest };
}

// onOk runs after every successful call: the board clears its error line there, as before.
export function createClient(
  getToken: () => string,
  fetchImpl: typeof fetch = (...a) => fetch(...a),
  onOk: () => void = () => {},
) {
  async function api<T>(path: string, method = "GET", body?: unknown, onHeaders?: (h: Headers) => void): Promise<T> {
    const res = await fetchImpl(path, {
      method,
      headers: { "content-type": "application/json", authorization: `Bearer ${getToken()}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw await apiError(res);
    const data = (res.status === 204 ? undefined : await res.json()) as T;
    onHeaders?.(res.headers);
    onOk();
    return data;
  }

  // GET /tasks/{id}/stream: the agent's events after `after`, then live ones until the task
  // finishes (the promise resolves then). fetch rather than EventSource, which can't send the token.
  async function streamTask(
    id: string,
    after: string,
    onEvent: (e: TaskEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetchImpl(`/tasks/${seg(id)}/stream?after=${encodeURIComponent(after)}`, {
      headers: { accept: "text/event-stream", authorization: `Bearer ${getToken()}` },
      signal,
    });
    if (!res.ok) throw await apiError(res);
    onOk();
    if (!res.body) return;
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      const { messages, rest } = parseSse(buffer + value);
      buffer = rest;
      for (const m of messages) onEvent({ id: m.id, type: m.event, data: JSON.parse(m.data) });
    }
  }

  return {
    listModels: () => api<Models>("/models"),
    listProjects: () => api<ProjectOut[]>("/projects"),
    createProject: (p: ProjectIn) => api<ProjectOut>("/projects", "POST", p),
    updateProject: (slug: string, patch: ProjectPatch) => api<ProjectOut>(`/projects/${seg(slug)}`, "PATCH", patch),
    // The done column holds the `doneLimit` newest items; `doneTotal` is how many there are in all.
    getBoard: async (slug: string, doneLimit: number): Promise<{ board: Board; doneTotal: number }> => {
      let doneTotal = 0;
      const board = await api<Board>(`/projects/${seg(slug)}/board?done_limit=${doneLimit}`, "GET", undefined, (h) => {
        doneTotal = Number(h.get("x-done-total") ?? 0);
      });
      return { board, doneTotal };
    },
    createItem: (slug: string, item: ItemIn) => api<ItemOut>(`/projects/${seg(slug)}/items`, "POST", item),
    importIssues: (slug: string) => api<ImportResult>(`/projects/${seg(slug)}/import-issues`, "POST"),
    getItem: (id: string) => api<ItemDetail>(`/items/${seg(id)}`),
    updateItem: (id: string, patch: ItemPatch) => api<ItemOut>(`/items/${seg(id)}`, "PATCH", patch),
    getItemGit: (id: string, diff = false) => api<GitSnapshot>(`/items/${seg(id)}/git${diff ? "?diff=true" : ""}`),
    startItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/start`, "POST"),
    reopenItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/reopen`, "POST"),
    cancelItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/cancel`, "POST"),
    deleteItem: (id: string) => api<void>(`/items/${seg(id)}`, "DELETE"),
    runItemNow: (id: string) => api<ItemOut>(`/items/${seg(id)}/run-now`, "POST"),
    answerRequest: (id: string, option: string, comment: string) =>
      api<{ advanced: boolean }>(`/requests/${seg(id)}/answer`, "POST", { option, comment }),
    getTask: (id: string) => api<TaskInfo>(`/tasks/${seg(id)}`),
    streamTask,
    getScheduler: () => api<SchedulerStatus>("/scheduler"),
    pauseScheduler: (reason = "") => api<SchedulerStatus>("/scheduler/pause", "POST", { reason }),
    resumeScheduler: () => api<SchedulerStatus>("/scheduler/resume", "POST", {}),
    setSchedulerLimits: (limits: Partial<SchedulerLimits>) =>
      api<SchedulerStatus>("/scheduler/limits", "PATCH", limits),
    // size=false skips measuring the copies on disk; empty filters are left out of the query.
    listWorkspaces: (f: { size?: boolean; phase?: WorkspacePhase | ""; project?: string } = {}) => {
      const q = new URLSearchParams();
      if (f.size === false) q.set("size", "false");
      if (f.phase) q.set("phase", f.phase);
      if (f.project) q.set("project", f.project);
      return api<WorkspaceList>(`/workspaces${q.size ? `?${q}` : ""}`);
    },
    deleteWorkspace: (id: string) => api<WorkspaceQueued>(`/workspaces/${seg(id)}`, "DELETE"),
    pruneWorkspaces: (body: PruneIn) => api<PruneResult>("/workspaces/prune", "POST", body),
    githubApp: () => api<GithubApp>("/github/app"),
    installationRepositories: (id: number) => api<Repository[]>(`/github/installations/${seg(id)}/repositories`),
  };
}

export type Client = ReturnType<typeof createClient>;

// localStorage can throw (private mode, disabled storage): the board then just forgets.
export const storage = {
  get: (key: "token" | "project" | "theme"): string => {
    try {
      return localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  },
  set: (key: "token" | "project" | "theme", value: string): void => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage unavailable */
    }
  },
};
