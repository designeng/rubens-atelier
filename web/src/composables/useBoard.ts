import { inject, onMounted, onUnmounted, provide, ref, type InjectionKey } from "vue";
import { createClient, storage } from "../api/client";
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
const NO_GITHUB: GithubApp = { configured: false, install_url: null, installations: [] };

/** Board state and actions; App.vue creates it once and provides it to the components. */
export function useBoard() {
  const token = ref(storage.get("token"));
  const projects = ref<ProjectOut[]>([]);
  const slug = ref("");
  const board = ref<Board | null>(null);
  const scheduler = ref<SchedulerStatus | null>(null);
  const openItemId = ref<string | null>(null);
  const item = ref<ItemDetail | null>(null);
  const github = ref<GithubApp>(NO_GITHUB);
  const models = ref<Models | null>(null);
  const error = ref("");

  const api = createClient(
    () => token.value,
    (...a) => fetch(...a),
    () => (error.value = ""),
  );
  const fail = (e: unknown) => {
    error.value = e instanceof Error ? e.message : String(e);
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
    const b = await api.getBoard(current);
    if (slug.value !== current) return;
    board.value = b;
    const id = openItemId.value;
    if (id) {
      const it = await api.getItem(id);
      if (openItemId.value === id) item.value = it;
    }
    scheduler.value = await api.getScheduler();
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
    projects.value = await api.listProjects();
    models.value = await api.listModels();
    const want = storage.get("project");
    slug.value = projects.value.find((p) => p.slug === want)?.slug ?? projects.value[0]?.slug ?? "";
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
    storage.set("project", value);
    closeItem();
    refresh().catch(fail);
  }

  const HASH = "#item=";
  const hashId = () => (location.hash.startsWith(HASH) ? decodeURIComponent(location.hash.slice(HASH.length)) : null);

  // The open story lives in the URL hash so it can be shared as a link.
  function syncFromHash(): void {
    const id = hashId();
    if (id === openItemId.value) return;
    if (id) openItem(id).catch(fail);
    else closeItem();
  }

  async function openItem(id: string): Promise<void> {
    if (hashId() !== id) history.pushState(null, "", HASH + encodeURIComponent(id));
    openItemId.value = id;
    const it = await api.getItem(id);
    if (openItemId.value === id) item.value = it;
  }

  function closeItem(): void {
    if (hashId()) history.pushState(null, "", location.pathname + location.search);
    openItemId.value = null;
    item.value = null;
  }

  // Board actions report failures in the header error line and refresh on success.
  const act = (p: Promise<unknown>): Promise<void> => p.then(() => refresh()).catch(fail);

  const startItem = (id: string) => act(api.startItem(id));
  const cancelItem = (id: string) => act(api.cancelItem(id));
  const reopenItem = (id: string) => act(api.reopenItem(id));
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
    if (t && t.isConnected && !t.closest("#detail, .card, dialog, header")) closeItem();
  };
  const onFocus = () => refreshGithub(); // back from github.com after installing the app

  onMounted(() => {
    loadProjects().catch(fail);
    refreshGithub();
    timer = setInterval(() => refresh().catch(fail), POLL_MS);
    window.addEventListener("focus", onFocus);
    window.addEventListener("hashchange", syncFromHash);
    window.addEventListener("popstate", syncFromHash);
    document.addEventListener("click", onDocClick);
  });
  onUnmounted(() => {
    clearInterval(timer);
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
    scheduler,
    openItemId,
    item,
    github,
    models,
    error,
    fail,
    refresh,
    refreshGithub,
    setToken,
    selectProject,
    openItem,
    closeItem,
    startItem,
    cancelItem,
    reopenItem,
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
