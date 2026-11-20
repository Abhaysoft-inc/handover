import {
  AlarmClock,
  Archive,
  BookOpen,
  CircleCheck,
  CircleX,
  FileCheck,
  FileText,
  Flag,
  FolderOpen,
  HardDriveUpload,
  Image,
  ListChecks,
  LoaderCircle,
  Mail,
  Newspaper,
  PenLine,
  ScanText,
  Search,
  Sparkles,
  Square,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { post } from "./api";
import { type DocState, type FeedTool, type RunState, useNow } from "./live";
import { Badge, Button, cx, duration, money } from "./ui";

const ICONS: Record<string, LucideIcon> = {
  list_drive_folder: FolderOpen,
  search_drive: Search,
  analyze_documents: ScanText,
  skip_documents: Archive,
  get_document_analysis: BookOpen,
  register_contract: FileCheck,
  flag_issue: Flag,
  publish_briefing: Newspaper,
  list_approved_documents: ListChecks,
  send_document_email: Mail,
  schedule_reminder: AlarmClock,
  file_to_drive: HardDriveUpload,
  complete_execution: CircleCheck,
  draft_document: PenLine,
  search_knowledge: Search,
  filter_contracts: ListChecks,
  get_contract: BookOpen,
  list_deadlines: AlarmClock,
};

export function RunHeader({ run, compact }: { run: RunState; compact?: boolean }) {
  const now = useNow();
  const running = run.status === "running";
  const live = run.mode === "live";
  return (
    <div className={cx("flex flex-wrap items-center gap-x-3 gap-y-2", compact ? "" : "border-b border-line px-5 py-3.5")}>
      <div className="flex items-center gap-2">
        {running ? (
          <span className="relative flex size-2.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-brand opacity-60" />
            <span className="relative inline-flex size-2.5 rounded-full bg-brand" />
          </span>
        ) : run.status === "completed" ? (
          <CircleCheck className="size-4 text-emerald-600" />
        ) : (
          <CircleX className="size-4 text-red-600" />
        )}
        <span className="text-sm font-semibold text-ink-950">{run.title || "Agent run"}</span>
      </div>
      <Badge tone={live ? "blue" : "violet"}>{live ? `Live · ${run.model ?? "AI"}` : "Simulation"}</Badge>
      <span className="text-xs tabular text-muted">{duration(run.startedAt, run.finishedAt, now)}</span>
      {run.usage && live && (
        <span className="text-xs tabular text-muted">
          {run.usage.requests} calls ·{" "}
          {run.usage.cost_usd !== null && run.usage.cost_usd !== undefined
            ? money(run.usage.cost_usd)
            : `${Math.round((run.usage.input_tokens + run.usage.output_tokens) / 1000)}k tokens`}
        </span>
      )}
      <div className="ml-auto">
        {running && (
          <Button variant="ghost" className="!px-2 !py-1 text-xs" icon={<Square className="size-3.5" />} onClick={() => void post(`/api/runs/${run.id}/cancel`)}>
            Stop
          </Button>
        )}
      </div>
    </div>
  );
}

function DocChip({ doc }: { doc: DocState }) {
  const state = doc.status;
  return (
    <div
      className={cx(
        "animate-rise flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 text-xs transition",
        state === "analyzed" && doc.employment && "border-emerald-200 bg-emerald-50/60",
        state === "analyzed" && !doc.employment && "border-line bg-slate-50",
        state === "queued" && "border-blue-200 bg-white",
        state === "reading" && "shimmer border-blue-200",
        state === "error" && "border-red-200 bg-red-50",
      )}
    >
      <div className="mt-0.5 shrink-0">
        {state === "analyzed" ? (
          <CircleCheck className={cx("size-3.5", doc.employment ? "text-emerald-600" : "text-slate-400")} />
        ) : state === "error" ? (
          <CircleX className="size-3.5 text-red-600" />
        ) : state === "reading" ? (
          <LoaderCircle className="size-3.5 animate-spin text-brand" />
        ) : (
          <span className="block size-3.5 rounded-full border border-dashed border-slate-300" />
        )}
      </div>
      <div className="min-w-0">
        <div className="truncate font-medium text-ink-900" title={`${doc.path}/${doc.name}`}>
          {doc.name}
        </div>
        <div className={cx("truncate text-[11px]", state === "reading" ? "text-brand" : "text-muted")}>
          {state === "queued" ? "Queued" : doc.detail}
        </div>
      </div>
    </div>
  );
}

function ToolRow({ tool, run }: { tool: FeedTool; run: RunState }) {
  const Icon = ICONS[tool.name] ?? Sparkles;
  const now = useNow(500);
  const active = tool.state === "running" || tool.state === "pending";
  const docs = tool.docs.map((id) => run.docs[id]).filter(Boolean);
  return (
    <div className="animate-rise relative pl-9">
      <div
        className={cx(
          "absolute left-0 top-0.5 flex size-[26px] items-center justify-center rounded-lg ring-1",
          active ? "bg-brand-50 text-brand ring-blue-200" : tool.state === "error" ? "bg-red-50 text-red-600 ring-red-200" : "bg-white text-ink-700 ring-line",
        )}
      >
        {active ? <LoaderCircle className="size-3.5 animate-spin" /> : <Icon className="size-3.5" />}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className={cx("text-[13.5px] font-medium", active ? "text-ink-950" : "text-ink-800")}>{tool.label}</span>
        <span className="font-mono text-[10.5px] text-slate-400">{tool.name}</span>
        {active && <span className="text-[11px] tabular text-brand">{duration(tool.startedAt, undefined, now)}</span>}
      </div>
      {tool.summary && (
        <div className={cx("mt-0.5 text-[12.5px]", tool.state === "error" ? "text-red-700" : "text-muted")}>{tool.summary}</div>
      )}
      {docs.length > 0 && (
        <div className="mt-2.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
          {docs.map((d) => (
            <DocChip key={d.id} doc={d} />
          ))}
        </div>
      )}
    </div>
  );
}

export function RunFeed({ run, maxHeight = "60vh", hideHeader }: { run: RunState; maxHeight?: string; hideHeader?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [run.items.length, run.docs, run.status]);
  return (
    <div>
      {!hideHeader && <RunHeader run={run} />}
      <div
        ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
        className="space-y-4 overflow-y-auto px-5 py-4"
        style={{ maxHeight }}
      >
        {run.items.length === 0 && run.status === "running" && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <LoaderCircle className="size-4 animate-spin text-brand" /> Starting the agent…
          </div>
        )}
        {run.items.map((item) =>
          item.kind === "note" ? (
            <div key={item.id} className="animate-rise relative pl-9">
              <div className="absolute left-0 top-0.5 flex size-[26px] items-center justify-center rounded-lg bg-ink-950 text-brass">
                <Sparkles className="size-3.5" />
              </div>
              <p className={cx("text-[13.5px] leading-relaxed", item.noteKind === "system" ? "text-amber-700" : "text-ink-800")}>{item.text}</p>
            </div>
          ) : (
            <ToolRow key={item.id} tool={item} run={run} />
          ),
        )}
        {run.status !== "running" && (run.summary || run.error) && (
          <div
            className={cx(
              "animate-rise rounded-lg px-3.5 py-2.5 text-[13px]",
              run.status === "completed" ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800",
            )}
          >
            <span className="font-semibold">{run.status === "completed" ? "Done. " : run.status === "cancelled" ? "Stopped. " : "Failed. "}</span>
            {run.error || run.summary}
          </div>
        )}
      </div>
    </div>
  );
}

// ── the Drive map that fills in as the agent explores ─────────────────────

export interface MapDoc {
  id: string;
  name: string;
  path: string;
  status: string;
  detail?: string;
  employment?: boolean | null;
  scanned?: boolean | null;
}

export function DriveMap({ docs, onOpen }: { docs: MapDoc[]; onOpen?: (d: MapDoc) => void }) {
  const groups = new Map<string, MapDoc[]>();
  for (const d of docs) {
    const k = d.path || "/";
    groups.set(k, [...(groups.get(k) ?? []), d]);
  }
  const folders = [...groups.keys()].sort();
  return (
    <div className="space-y-3">
      {folders.map((f) => (
        <div key={f}>
          <div className="mb-1 flex items-center gap-1.5 text-[11.5px] font-semibold text-muted">
            <FolderOpen className="size-3.5" /> {f === "/" ? "Drive root" : f}
          </div>
          <div className="space-y-0.5">
            {groups.get(f)!.map((d) => {
              const Icon = d.scanned ? Image : FileText;
              return (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => onOpen?.(d)}
                  className="animate-rise flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[12.5px] hover:bg-slate-50"
                >
                  <Icon className={cx("size-3.5 shrink-0", d.status === "skipped" ? "text-slate-300" : "text-slate-500")} />
                  <span className={cx("min-w-0 flex-1 truncate", d.status === "skipped" ? "text-slate-400 line-through decoration-slate-300" : "text-ink-900")}>
                    {d.name}
                  </span>
                  <DocStatus d={d} />
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function DocStatus({ d }: { d: MapDoc }) {
  if (d.status === "analyzed")
    return d.employment ? <Badge tone="green">employment</Badge> : <Badge>context</Badge>;
  if (d.status === "skipped") return <Badge>set aside</Badge>;
  if (d.status === "reading" || d.status === "analyzing")
    return (
      <Badge tone="blue">
        <LoaderCircle className="size-3 animate-spin" /> reading
      </Badge>
    );
  if (d.status === "queued") return <Badge tone="blue">queued</Badge>;
  if (d.status === "error") return <Badge tone="red">error</Badge>;
  return <span className="text-[11px] text-slate-400">found</span>;
}
