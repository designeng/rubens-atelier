import type {
  Board,
  GithubApp,
  GitSnapshot,
  ItemDetail,
  ItemIn,
  ItemOut,
  ProjectIn,
  ProjectOut,
  ItemPatch,
  ProjectPatch,
  Repository,
  SchedulerLimits,
  SchedulerStatus,
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

// onOk runs after every successful call: the board clears its error line there, as before.
export function createClient(
  getToken: () => string,
  fetchImpl: typeof fetch = (...a) => fetch(...a),
  onOk: () => void = () => {},
) {
  async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const res = await fetchImpl(path, {
      method,
      headers: { "content-type": "application/json", authorization: `Bearer ${getToken()}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const detail: unknown = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
      const msg = detail || res.statusText;
      throw new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg), res.status);
    }
    const data = (await res.json()) as T;
    onOk();
    return data;
  }

  return {
    listProjects: () => api<ProjectOut[]>("/projects"),
    createProject: (p: ProjectIn) => api<ProjectOut>("/projects", "POST", p),
    updateProject: (slug: string, patch: ProjectPatch) => api<ProjectOut>(`/projects/${seg(slug)}`, "PATCH", patch),
    getBoard: (slug: string) => api<Board>(`/projects/${seg(slug)}/board`),
    createItem: (slug: string, item: ItemIn) => api<ItemOut>(`/projects/${seg(slug)}/items`, "POST", item),
    getItem: (id: string) => api<ItemDetail>(`/items/${seg(id)}`),
    updateItem: (id: string, patch: ItemPatch) => api<ItemOut>(`/items/${seg(id)}`, "PATCH", patch),
    getItemGit: (id: string, diff = false) => api<GitSnapshot>(`/items/${seg(id)}/git${diff ? "?diff=true" : ""}`),
    startItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/start`, "POST"),
    reopenItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/reopen`, "POST"),
    cancelItem: (id: string) => api<ItemOut>(`/items/${seg(id)}/cancel`, "POST"),
    answerRequest: (id: string, option: string, comment: string) =>
      api<{ advanced: boolean }>(`/requests/${seg(id)}/answer`, "POST", { option, comment }),
    getScheduler: () => api<SchedulerStatus>("/scheduler"),
    pauseScheduler: (reason = "") => api<SchedulerStatus>("/scheduler/pause", "POST", { reason }),
    resumeScheduler: () => api<SchedulerStatus>("/scheduler/resume", "POST", {}),
    setSchedulerLimits: (limits: Partial<SchedulerLimits>) =>
      api<SchedulerStatus>("/scheduler/limits", "PATCH", limits),
    githubApp: () => api<GithubApp>("/github/app"),
    installationRepositories: (id: number) => api<Repository[]>(`/github/installations/${seg(id)}/repositories`),
  };
}

export type Client = ReturnType<typeof createClient>;

// localStorage can throw (private mode, disabled storage): the board then just forgets.
export const storage = {
  get: (key: "token" | "project"): string => {
    try {
      return localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  },
  set: (key: "token" | "project", value: string): void => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage unavailable */
    }
  },
};
