import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, type Run, type RunEvent } from "./api";

// ── server-sent events ────────────────────────────────────────────────────

type Listener = (ev: RunEvent) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;
let connected = false;
const connListeners = new Set<() => void>();

export function connectEvents() {
  if (source) return;
  source = new EventSource("/api/events");
  source.onopen = () => {
    connected = true;
    connListeners.forEach((l) => l());
  };
  source.onerror = () => {
    connected = false;
    connListeners.forEach((l) => l());
  };
  source.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data) as RunEvent;
      runStore.apply(ev);
      listeners.forEach((l) => l(ev));
    } catch {
      /* ignore malformed */
    }
  };
}

export function onEvent(l: Listener) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useConnected() {
  return useSyncExternalStore(
    (cb) => {
      connListeners.add(cb);
      return () => connListeners.delete(cb);
    },
    () => connected,
  );
}

// ── data fetching that refreshes when the server says its scope changed ───

export function useApi<T>(path: string | null, scopes: string[]) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const scopeKey = scopes.join(",");

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(undefined);
    void reload();
  }, [reload]);

  useEffect(() => {
    const wanted = new Set(scopeKey.split(","));
    return onEvent((ev) => {
      const hit =
        (ev.type === "data.changed" && (ev.data?.scopes as string[] | undefined)?.some((s) => wanted.has(s))) ||
        (ev.type.startsWith("run.") && ev.type !== "run.started" && wanted.has("runs"));
      if (hit) {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void reload(), 180);
      }
    });
  }, [scopeKey, reload]);

  return { data, error, loading, reload, setData };
}

// ── live run feed ─────────────────────────────────────────────────────────

export type ToolState = "pending" | "running" | "ok" | "error";

export interface FeedTool {
  kind: "tool";
  id: string;
  name: string;
  label: string;
  state: ToolState;
  summary?: string;
  startedAt: string;
  finishedAt?: string;
  docs: string[];
  input?: unknown;
}
export interface FeedNote {
  kind: "note";
  id: string;
  text: string;
  noteKind: string;
  ts: string;
}
export type FeedItem = FeedTool | FeedNote;

export interface DocState {
  id: string;
  name: string;
  path: string;
  status: string;
  detail: string;
  employment?: boolean;
  scanned?: boolean;
  language?: string;
}

export interface RunState {
  id: string;
  kind: string;
  title: string;
  mode: string;
  model?: string;
  status: string;
  startedAt: string;
  finishedAt?: string;
  summary?: string;
  error?: string | null;
  usage?: { cost_usd: number | null; requests: number; input_tokens: number; output_tokens: number };
  items: FeedItem[];
  docs: Record<string, DocState>;
  text: string;
  answer?: string;
  conversationId?: string;
  lastSeq: number;
}

function emptyRun(id: string, kind = "", ts = new Date().toISOString()): RunState {
  return { id, kind, title: "", mode: "", status: "running", startedAt: ts, items: [], docs: {}, text: "", lastSeq: 0 };
}

function reduce(run: RunState, ev: RunEvent): RunState {
  if (ev.seq !== undefined) {
    if (ev.seq <= run.lastSeq) return run;
  }
  const r: RunState = { ...run, lastSeq: ev.seq ?? run.lastSeq };
  const d = ev.data ?? {};
  const findTool = (id: string) => r.items.findIndex((i) => i.kind === "tool" && i.id === id);
  switch (ev.type) {
    case "run.started":
      r.kind = d.kind ?? r.kind;
      r.title = d.title ?? r.title;
      r.mode = d.mode ?? r.mode;
      r.model = d.model;
      r.startedAt = ev.ts;
      r.status = "running";
      break;
    case "run.model":
      r.model = d.model;
      break;
    case "agent.note":
      r.items = [...r.items, { kind: "note", id: `n${ev.seq}`, text: d.text, noteKind: d.kind ?? "update", ts: ev.ts }];
      break;
    case "tool.pending": {
      if (findTool(d.call_id) < 0)
        r.items = [
          ...r.items,
          { kind: "tool", id: d.call_id, name: d.name, label: prettyTool(d.name), state: "pending", startedAt: ev.ts, docs: [] },
        ];
      break;
    }
    case "tool.started": {
      const i = findTool(d.call_id);
      const base: FeedTool = { kind: "tool", id: d.call_id, name: d.name, label: d.label || prettyTool(d.name), state: "running", startedAt: ev.ts, docs: [], input: d.input };
      if (i < 0) r.items = [...r.items, base];
      else {
        const items = [...r.items];
        items[i] = { ...(items[i] as FeedTool), label: base.label, state: "running", startedAt: ev.ts, input: d.input };
        r.items = items;
      }
      break;
    }
    case "tool.finished": {
      const i = findTool(d.call_id);
      if (i >= 0) {
        const items = [...r.items];
        items[i] = { ...(items[i] as FeedTool), state: d.ok ? "ok" : "error", summary: d.summary, finishedAt: ev.ts };
        r.items = items;
      }
      break;
    }
    case "doc.status": {
      r.docs = {
        ...r.docs,
        [d.document_id]: {
          id: d.document_id,
          name: d.name,
          path: d.path,
          status: d.status,
          detail: d.detail ?? "",
          employment: d.employment ?? r.docs[d.document_id]?.employment,
          scanned: d.scanned ?? r.docs[d.document_id]?.scanned,
          language: d.language ?? r.docs[d.document_id]?.language,
        },
      };
      if (d.status === "queued") {
        // attach to the analyse batch that is running
        for (let k = r.items.length - 1; k >= 0; k--) {
          const it = r.items[k];
          if (it.kind === "tool" && it.name === "analyze_documents" && it.state === "running") {
            if (!it.docs.includes(d.document_id)) {
              const items = [...r.items];
              items[k] = { ...it, docs: [...it.docs, d.document_id] };
              r.items = items;
            }
            break;
          }
        }
      }
      break;
    }
    case "usage":
      r.usage = { cost_usd: d.cost_usd, requests: d.requests, input_tokens: d.input_tokens, output_tokens: d.output_tokens };
      break;
    case "agent.text_delta":
      r.text = r.text + (d.text ?? "");
      break;
    case "answer.completed":
      r.answer = d.text;
      r.conversationId = d.conversation_id;
      break;
    case "run.completed":
    case "run.failed":
    case "run.cancelled":
      r.status = ev.type.slice(4);
      r.finishedAt = ev.ts;
      r.summary = d.summary;
      r.error = d.error;
      if (d.usage) r.usage = d.usage;
      r.items = r.items.map((i) => (i.kind === "tool" && (i.state === "running" || i.state === "pending") ? { ...i, state: r.status === "completed" ? "ok" : "error" } : i));
      break;
  }
  return r;
}

const TOOL_LABELS: Record<string, string> = {
  list_drive_folder: "Opening a folder",
  search_drive: "Searching the Drive",
  analyze_documents: "Reading documents",
  skip_documents: "Setting files aside",
  get_document_analysis: "Re-reading an analysis",
  register_contract: "Registering a contract",
  flag_issue: "Flagging an issue",
  publish_briefing: "Writing the briefing",
  list_approved_documents: "Collecting approved documents",
  send_document_email: "Sending an email",
  schedule_reminder: "Scheduling a reminder",
  file_to_drive: "Filing to Drive",
  complete_execution: "Closing out",
  search_knowledge: "Searching the knowledge base",
  filter_contracts: "Filtering contracts",
  get_contract: "Opening a contract",
  list_deadlines: "Listing deadlines",
  draft_document: "Drafting",
};
export const prettyTool = (name: string) => TOOL_LABELS[name] ?? name.replace(/_/g, " ");

class RunStore {
  runs = new Map<string, RunState>();
  latestByKind = new Map<string, string>();
  private subs = new Set<() => void>();
  version = 0;

  subscribe = (cb: () => void) => {
    this.subs.add(cb);
    return () => {
      this.subs.delete(cb);
    };
  };
  private emit() {
    this.version++;
    this.subs.forEach((s) => s());
  }

  apply(ev: RunEvent) {
    if (!ev.run_id) return;
    const prev = this.runs.get(ev.run_id) ?? emptyRun(ev.run_id, ev.kind, ev.ts);
    const next = reduce(prev, ev);
    if (next === prev && this.runs.has(ev.run_id)) return;
    this.runs.set(ev.run_id, next);
    if (ev.type === "run.started" && ev.kind) this.latestByKind.set(ev.kind, ev.run_id);
    this.emit();
  }

  async load(runId: string) {
    const run = await api<Run>(`/api/runs/${runId}`);
    let state = emptyRun(run.id, run.kind, run.started_at);
    for (const ev of run.events ?? []) state = reduce(state, ev);
    const live = this.runs.get(runId);
    if (live && live.lastSeq > state.lastSeq) return; // SSE already ahead
    state.title = state.title || run.title;
    state.status = run.status;
    this.runs.set(runId, state);
    if (!this.latestByKind.has(run.kind)) this.latestByKind.set(run.kind, run.id);
    this.emit();
  }
}

export const runStore = new RunStore();

export function useRun(runId: string | null | undefined): RunState | undefined {
  useSyncExternalStore(runStore.subscribe, () => runStore.version);
  useEffect(() => {
    if (runId && !runStore.runs.has(runId)) void runStore.load(runId).catch(() => undefined);
  }, [runId]);
  return runId ? runStore.runs.get(runId) : undefined;
}

/** The most recent run of the given kind (loads history on first use). */
export function useLatestRunId(kind: string): string | undefined {
  useSyncExternalStore(runStore.subscribe, () => runStore.version);
  useEffect(() => {
    if (runStore.latestByKind.has(kind)) return;
    void api<Run[]>(`/api/runs?limit=40`).then((runs) => {
      const r = runs.find((x) => x.kind === kind);
      if (r && !runStore.latestByKind.has(kind)) {
        runStore.latestByKind.set(kind, r.id);
        void runStore.load(r.id);
      }
    });
  }, [kind]);
  return runStore.latestByKind.get(kind);
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

// ── hash router ───────────────────────────────────────────────────────────

export function useRoute(): [string[], (to: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState<string[]>(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((to: string) => {
    window.location.hash = to.startsWith("#") ? to : `#/${to.replace(/^\//, "")}`;
  }, []);
  return [parts, go];
}

export function navigate(to: string) {
  window.location.hash = `#/${to.replace(/^\//, "")}`;
}
