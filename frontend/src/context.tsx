import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Overview, Status } from "./api";
import { onEvent, useApi } from "./live";

interface AppData {
  status?: Status;
  overview?: Overview;
  reloadStatus: () => void;
  reloadOverview: () => void;
}

const Ctx = createContext<AppData>({ reloadStatus: () => undefined, reloadOverview: () => undefined });

export function AppDataProvider({ children }: { children: ReactNode }) {
  const status = useApi<Status>("/api/status", ["status", "runs"]);
  const overview = useApi<Overview>("/api/overview", ["overview", "contracts", "documents", "drafts", "runs", "briefing", "reminders", "filings", "outbox", "issues"]);
  return (
    <Ctx.Provider
      value={{
        status: status.data,
        overview: overview.data,
        reloadStatus: () => void status.reload(),
        reloadOverview: () => void overview.reload(),
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export const useAppData = () => useContext(Ctx);

// ── toasts ────────────────────────────────────────────────────────────────

export interface Toast {
  id: number;
  tone: "info" | "success" | "error" | "reminder";
  title: string;
  body?: string;
}

const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children, render }: { children: ReactNode; render: (toasts: Toast[], dismiss: (id: number) => void) => ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = ++seq.current;
      setToasts((all) => [...all.slice(-4), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.tone === "error" ? 9000 : 5500);
    },
    [dismiss],
  );

  // surface background activity: reminders firing and runs finishing
  useEffect(
    () =>
      onEvent((ev) => {
        if (ev.type === "reminder.fired") {
          push({ tone: "reminder", title: "Reminder sent", body: `${ev.data.subject} → ${(ev.data.recipients ?? []).join(", ")}` });
        } else if (ev.type === "run.completed" && ev.kind !== "ask") {
          push({ tone: "success", title: "Agent finished", body: ev.data.summary });
        } else if (ev.type === "run.failed") {
          push({ tone: "error", title: "Agent run failed", body: ev.data.error });
        }
      }),
    [push],
  );

  return (
    <ToastCtx.Provider value={push}>
      {children}
      {render(toasts, dismiss)}
    </ToastCtx.Provider>
  );
}
