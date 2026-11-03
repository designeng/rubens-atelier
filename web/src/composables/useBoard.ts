import { inject, onMounted, onUnmounted, provide, ref, type InjectionKey } from "vue";
import { ApiError, createClient, storage } from "../api/client";
import { toasts } from "../toasts";
import type {
  Board,
  GithubApp,
  ImportResult,
  ItemDetail,
  ItemIn,
  ItemPatch,
  Models,
  ProjectIn,
  ProjectOut,
  ProjectPatch,
  SchedulerLimits,
  SchedulerStatus,
} from "../api/types";

export const POLL_MS = 5000;
export const MAX_DONE = 500; // the server maximum
export const DONE_PAGE = 50; // the server default; each "show more" adds this many

/** More done items exist than are shown, and the limit can still grow. */
export const canShowMoreDone = (total: number, shown: number, limit: number): boolean =>
  total > shown && limit < MAX_DONE;
const NO_GITHUB: GithubApp = { configured: false, install_url: null, installations: [] };

// The open story and its drawer tab live in the URL hash ("#item=<id>&tab=runs") so they can be shared as a link.
export type ItemTab = "overview" | "runs" | "workspace";
const TABS: ItemTab[] = ["overview", "runs", "workspace"];

export function parseHash(hash: string): { item: string | null; tab: ItemTab } {
  const q = new URLSearchParams(hash.replace(/^#/, ""));
  const tab = q.get("tab") as ItemTab | null;
  return { item: q.get("item") || null, tab: tab && TABS.includes(tab) ? tab : "overview" };
}

export function itemHash(id: string, tab: ItemTab = "overview"): string {
  return `#item=${encodeURIComponent(id)}` + (tab === "overview" ? "" : `&tab=${tab}`);
}

/** Board state and actions; App.vue creates it once and provides it to the components. */
export function useBoard() {
  const token = ref(storage.get("token"));
  const projects = ref<ProjectOut[]>([]);
  const slug = ref("");
  const board = ref<Board | null>(null);
  const doneLimit = ref(DONE_PAGE);
  const doneTotal = ref(0);
  const proposedNotes = ref(0); // agents' notes waiting for review in the project's memory
  const scheduler = ref<SchedulerStatus | null>(null);
  const openItemId = ref<string | null>(null);
  const item = ref<ItemDetail | null>(null);
  const github = ref<GithubApp>(NO_GITHUB);
  const models = ref<Models | null>(null);
  const error = ref("");
  // The API refused the token: the board shows the sign-in prompt instead of an error.
  const unauthorized = ref(false);
  // The first project list has arrived (or failed): before that the board shows placeholders, not "no project".
  const loaded = ref(false);

  const api = createClient(
    () => token.value,
    (...a) => fetch(...a),
    () => {
      error.value = "";
      unauthorized.value = false;
    },
  );
  // Board actions report failures as an error toast; a 401 switches to the sign-in prompt instead.
  const fail = (e: unknown) => {
    error.value = e instanceof Error ? e.message : String(e);
    if (e instanceof ApiError && e.status === 401) unauthorized.value = true;
    else toasts.push(error.value, "error");
  };

  // One refresh at a time: a poll or an action during a slow one schedules a single rerun.
  let inFlight: Promise<void> | null = null;
  let again = false;

  async function refreshOnce(): Promise<void> {
    const current = slug.value;
    if (!current) {
      board.value = null;
      return;
    }
    const { board: b, doneTotal: total, proposedNotes: proposed } = await api.getBoard(current, doneLimit.value);
    if (slug.value !== current) return;
    board.value = b;
    doneTotal.value = total;
    proposedNotes.value = proposed;
    const id = openItemId.value;
    if (id) {
      const it = await api.getItem(id);
      if (openItemId.value === id) item.value = it;
    }
    scheduler.value = await api.getScheduler();
    projects.value = await api.listProjects(); // the header shows what the project has spent
  }

  function refresh(): Promise<void> {
    if (inFlight) {
      again = true;
      return inFlight;
    }
    inFlight = (async () => {
      try {
        do {
          again = false;
          await refreshOnce();
        } while (again);
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  }

  async function loadProjects(): Promise<void> {
    try {
      projects.value = await api.listProjects();
      models.value = await api.listModels();
      const want = storage.get("project");
      slug.value = projects.value.find((p) => p.slug === want)?.slug ?? projects.value[0]?.slug ?? "";
    } finally {
      loaded.value = true;
    }
    await refresh();
    syncFromHash();
  }

  async function refreshGithub(): Promise<void> {
    github.value = await api.githubApp().catch(() => NO_GITHUB);
  }

  function setToken(value: string): void {
    token.value = value;
    storage.set("token", value);
    loadProjects().catch(fail);
    refreshGithub();
  }

  function selectProject(value: string): void {
    slug.value = value;
    doneLimit.value = DONE_PAGE;
    proposedNotes.value = 0;
    storage.set("project", value);
    closeItem();
    refresh().catch(fail);
  }

  const itemTab = ref<ItemTab>(parseHash(location.hash).tab);
  const hashId = () => parseHash(location.hash).item;

  function syncFromHash(): void {
    const { item: id, tab } = parseHash(location.hash);
    itemTab.value = tab;
    if (id === openItemId.value) return;
    if (id) openItem(id, tab).catch(fail);
    else closeItem();
  }

  async function openItem(id: string, tab: ItemTab = "overview"): Promise<void> {
    if (hashId() !== id) history.pushState(null, "", itemHash(id, tab));
    itemTab.value = tab;
    openItemId.value = id;
    const it = await api.getItem(id);
    if (openItemId.value === id) item.value = it;
  }

  function closeItem(): void {
    if (hashId()) history.pushState(null, "", location.pathname + location.search);
    openItemId.value = null;
    item.value = null;
  }

  // Switching tabs replaces the history entry: back closes the story rather than stepping through tabs.
  function setItemTab(tab: ItemTab): void {
    itemTab.value = tab;
    if (openItemId.value) history.replaceState(null, "", itemHash(openItemId.value, tab));
  }

  function showMoreDone(): void {
    doneLimit.value = Math.min(doneLimit.value + DONE_PAGE, MAX_DONE);
    refresh().catch(fail);
  }

  // Board actions report failures in the header error line and refresh on success.
  const act = (p: Promise<unknown>): Promise<void> => p.then(() => refresh()).catch(fail);

  // A story from an outside author's issue is untrusted text an agent would act on: ask first.
  const startItem = (id: string): Promise<void> => {
    const known = item.value?.id === id ? item.value : Object.values(board.value ?? {}).flat().find((i) => i.id === id);
    if (known?.external) {
      const ok = window.confirm(
        `This story comes from an issue by an external author (@${known.author ?? "unknown"}).\n` +
          "Its text is untrusted and may try to steer the agent, which can run commands.\n\nStart it anyway?",
      );
      if (!ok) return Promise.resolve();
      return act(api.startItem(id, true));
    }
    return act(api.startItem(id));
  };
  const cancelItem = (id: string) => act(api.cancelItem(id));
  const reopenItem = (id: string) => act(api.reopenItem(id));
  const openPr = (id: string) => act(api.openPr(id));
  const deleteItem = (id: string) =>
    act(
      api.deleteItem(id).then(() => {
        if (openItemId.value === id) closeItem();
      }),
    );
  const runItemNow = (id: string) => act(api.runItemNow(id));
  const answer = (requestId: string, option: string, comment: string) =>
    act(api.answerRequest(requestId, option, comment));
  const togglePause = () => act(isPaused() ? api.resumeScheduler() : api.pauseScheduler());
  const isPaused = () => !!scheduler.value?.reason.startsWith("paused");
  const setLimits = (limits: Partial<SchedulerLimits>) => act(api.setSchedulerLimits(limits));

  // Dialog actions throw, so the dialog can show the error next to its fields.
  async function createItem(item: ItemIn): Promise<void> {
    await api.createItem(slug.value, item);
    await refresh();
  }

  async function importIssues(): Promise<ImportResult> {
    const res = await api.importIssues(slug.value);
    await refresh();
    return res;
  }

  async function createProject(project: ProjectIn): Promise<void> {
    await api.createProject(project);
    storage.set("project", project.slug);
    await loadProjects();
  }

  async function updateItem(id: string, patch: ItemPatch): Promise<void> {
    await api.updateItem(id, patch);
    await refresh();
  }

  async function updateProject(patch: ProjectPatch): Promise<void> {
    await api.updateProject(slug.value, patch);
    await loadProjects();
  }

  let timer: ReturnType<typeof setInterval> | undefined;
  // A click outside the open story (not on a card, dialog or the header) closes it.
  const onDocClick = (e: MouseEvent) => {
    if (!openItemId.value) return;
    const t = e.target as Element | null;
    if (t && t.isConnected && !t.closest("#detail, .card, dialog, header, .toasts")) closeItem();
  };
  const onFocus = () => refreshGithub(); // back from github.com after installing the app
  // A hidden tab doesn't poll; coming back refreshes at once.
  const onVisibility = () => {
    if (document.hidden) {
      clearInterval(timer);
      timer = undefined;
    } else if (timer === undefined) {
      refresh().catch(fail);
      timer = setInterval(() => refresh().catch(fail), POLL_MS);
    }
  };

  onMounted(() => {
    loadProjects().catch(fail);
    refreshGithub();
    if (!document.hidden) timer = setInterval(() => refresh().catch(fail), POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    window.addEventListener("hashchange", syncFromHash);
    window.addEventListener("popstate", syncFromHash);
    document.addEventListener("click", onDocClick);
  });
  onUnmounted(() => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("hashchange", syncFromHash);
    window.removeEventListener("popstate", syncFromHash);
    document.removeEventListener("click", onDocClick);
  });

  return {
    api,
    token,
    projects,
    slug,
    board,
    doneTotal,
    proposedNotes,
    doneLimit,
    scheduler,
    openItemId,
    item,
    github,
    models,
    error,
    unauthorized,
    loaded,
    itemTab,
    setItemTab,
    fail,
    refresh,
    showMoreDone,
    refreshGithub,
    setToken,
    selectProject,
    openItem,
    closeItem,
    startItem,
    cancelItem,
    reopenItem,
    openPr,
    deleteItem,
    runItemNow,
    answer,
    togglePause,
    isPaused,
    setLimits,
    createItem,
    importIssues,
    createProject,
    updateItem,
    updateProject,
  };
}

export type BoardStore = ReturnType<typeof useBoard>;

const key: InjectionKey<BoardStore> = Symbol("board");

export function provideBoard(store: BoardStore): void {
  provide(key, store);
}

export function useBoardStore(): BoardStore {
  const store = inject(key);
  if (!store) throw new Error("useBoardStore() outside of App");
  return store;
}
