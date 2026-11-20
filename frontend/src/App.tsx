import { BellRing, Briefcase, CircleCheck, CircleX, FilePen, Inbox, Info, LayoutDashboard, MessageSquareText, Settings as SettingsIcon, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { AppDataProvider, ToastProvider, useAppData, type Toast } from "./context";
import { useConnected, useRoute } from "./live";
import { Badge, cx } from "./ui";
import { Ask } from "./views/Ask";
import { Drafts } from "./views/Drafts";
import { Execution } from "./views/Execution";
import { Home } from "./views/Home";
import { Portfolio } from "./views/Portfolio";
import { Settings } from "./views/Settings";

interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  step?: string;
}

const NAV: NavItem[] = [
  { key: "", label: "Mission control", icon: LayoutDashboard, step: "1–2" },
  { key: "portfolio", label: "Portfolio", icon: Briefcase, step: "3" },
  { key: "drafts", label: "Drafts", icon: FilePen, step: "4" },
  { key: "execution", label: "Execution", icon: Inbox, step: "5" },
  { key: "ask", label: "Ask the archive", icon: MessageSquareText, step: "6" },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex size-8 items-center justify-center rounded-lg bg-brass text-ink-950 shadow-[inset_0_-2px_0_rgba(0,0,0,0.15)]">
        <svg viewBox="0 0 32 32" className="size-5" aria-hidden>
          <path d="M8 24V8h3.6v6.1h8.8V8H24v16h-3.6v-6.3h-8.8V24z" fill="currentColor" />
        </svg>
      </div>
      <div>
        <div className="text-[15px] font-semibold leading-tight tracking-tight text-white">Handover</div>
        <div className="text-[11px] leading-tight text-slate-400">The contract desk for new counsel</div>
      </div>
    </div>
  );
}

function Sidebar({ current }: { current: string }) {
  const { status, overview } = useAppData();
  const connected = useConnected();
  const urgent = (overview?.contracts.urgency.missed ?? 0) + (overview?.contracts.urgency.critical ?? 0);
  const counts: Record<string, ReactNode> = {
    portfolio: urgent ? <Badge tone="rose">{urgent}</Badge> : overview?.contracts.total ? <span className="text-xs text-slate-400">{overview.contracts.total}</span> : null,
    drafts: overview?.drafts.draft ? <Badge tone="amber">{overview.drafts.draft}</Badge> : null,
    execution: overview?.reminders.scheduled ? <span className="text-xs text-slate-400">{overview.reminders.scheduled}</span> : null,
  };
  return (
    <aside className="flex w-[244px] shrink-0 flex-col bg-ink-950 px-3 py-4 text-slate-300">
      <div className="px-2 pb-6">
        <Logo />
      </div>
      <nav className="space-y-0.5">
        {NAV.map((n) => {
          const active = current === n.key;
          return (
            <a
              key={n.key}
              href={`#/${n.key}`}
              className={cx(
                "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition",
                active ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-slate-200",
              )}
            >
              <n.icon className={cx("size-4", active ? "text-brass" : "")} />
              <span className="flex-1">{n.label}</span>
              {counts[n.key]}
            </a>
          );
        })}
      </nav>
      <div className="mt-6 px-2.5 text-[11px] leading-relaxed text-slate-500">
        Ingest → Analyze → Review → Draft → Execute → Know. Each step is an agent run you can watch live and approve.
      </div>
      <div className="mt-auto space-y-3">
        <a
          href="#/settings"
          className={cx(
            "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium",
            current === "settings" ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-slate-200",
          )}
        >
          <SettingsIcon className="size-4" /> Settings
        </a>
        <div className="rounded-xl bg-white/5 px-3 py-3">
          <div className="flex items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-full bg-brand text-[11px] font-semibold text-white">
              {(status?.counsel.name ?? "C").split(" ").map((p) => p[0]).join("").slice(0, 2)}
            </div>
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-medium text-slate-100">{status?.counsel.name ?? "Counsel"}</div>
              <div className="truncate text-[11px] text-slate-500">In-house counsel · {status?.company}</div>
            </div>
          </div>
          <div className="mt-2.5 flex items-center justify-between text-[11px]">
            <span className={cx("inline-flex items-center gap-1.5", status?.mode === "live" ? "text-emerald-400" : "text-violet-300")}>
              <span className={cx("size-1.5 rounded-full", status?.mode === "live" ? "bg-emerald-400" : "bg-violet-300")} />
              {status ? (status.mode === "live" ? `Live · ${status.provider_label}` : "Simulation") : "…"}
            </span>
            <span className={cx("inline-flex items-center gap-1", connected ? "text-slate-500" : "text-amber-400")} title="Live event stream">
              <span className={cx("size-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-amber-400")} />
              {connected ? "streaming" : "reconnecting"}
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}

function Toasts({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[380px] flex-col gap-2">
      {toasts.map((t) => {
        const Icon = t.tone === "success" ? CircleCheck : t.tone === "error" ? CircleX : t.tone === "reminder" ? BellRing : Info;
        return (
          <div key={t.id} className="animate-rise pointer-events-auto flex gap-3 rounded-xl border border-line bg-white px-4 py-3 shadow-lg">
            <Icon
              className={cx(
                "mt-0.5 size-4 shrink-0",
                t.tone === "success" && "text-emerald-600",
                t.tone === "error" && "text-red-600",
                t.tone === "reminder" && "text-amber-600",
                t.tone === "info" && "text-brand",
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-ink-950">{t.title}</div>
              {t.body && <div className="mt-0.5 line-clamp-3 text-[12.5px] text-muted">{t.body}</div>}
            </div>
            <button type="button" onClick={() => dismiss(t.id)} className="self-start text-slate-400 hover:text-slate-600">
              <X className="size-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function Shell() {
  const [parts] = useRoute();
  const page = parts[0] ?? "";
  return (
    <div className="flex h-full">
      <Sidebar current={page} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        {page === "" && <Home />}
        {page === "portfolio" && <Portfolio contractId={parts[1]} />}
        {page === "drafts" && <Drafts draftId={parts[1]} />}
        {page === "execution" && <Execution />}
        {page === "ask" && <Ask />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AppDataProvider>
      <ToastProvider render={(toasts, dismiss) => <Toasts toasts={toasts} dismiss={dismiss} />}>
        <Shell />
      </ToastProvider>
    </AppDataProvider>
  );
}
