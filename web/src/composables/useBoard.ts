import { inject, onMounted, onUnmounted, provide, ref, type InjectionKey } from "vue";
import { createClient, storage } from "../api/client";
import type { Board, GithubApp, ItemDetail, ItemIn, ProjectIn, ProjectOut, SchedulerStatus } from "../api/types";

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
    const want = storage.get("project");
    slug.value = projects.value.find((p) => p.slug === want)?.slug ?? projects.value[0]?.slug ?? "";
    await refresh();
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

  async function openItem(id: string): Promise<void> {
    openItemId.value = id;
    const it = await api.getItem(id);
    if (openItemId.value === id) item.value = it;
  }

  function closeItem(): void {
    openItemId.value = null;
    item.value = null;
  }

  // Board actions report failures in the header error line and refresh on success.
  const act = (p: Promise<unknown>): Promise<void> => p.then(() => refresh()).catch(fail);

  const startItem = (id: string) => act(api.startItem(id));
  const cancelItem = (id: string) => act(api.cancelItem(id));
  const answer = (requestId: string, option: string, comment: string) =>
    act(api.answerRequest(requestId, option, comment));
  const togglePause = () => act(isPaused() ? api.resumeScheduler() : api.pauseScheduler());
  const isPaused = () => !!scheduler.value?.reason.startsWith("paused");

  // Dialog actions throw, so the dialog can show the error next to its fields.
  async function createItem(item: ItemIn): Promise<void> {
    await api.createItem(slug.value, item);
    await refresh();
  }

  async function createProject(project: ProjectIn): Promise<void> {
    await api.createProject(project);
    storage.set("project", project.slug);
    await loadProjects();
  }

  let timer: ReturnType<typeof setInterval> | undefined;
  const onFocus = () => refreshGithub(); // back from github.com after installing the app

  onMounted(() => {
    loadProjects().catch(fail);
    refreshGithub();
    timer = setInterval(() => refresh().catch(fail), POLL_MS);
    window.addEventListener("focus", onFocus);
  });
  onUnmounted(() => {
    clearInterval(timer);
    window.removeEventListener("focus", onFocus);
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
    answer,
    togglePause,
    isPaused,
    createItem,
    createProject,
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
