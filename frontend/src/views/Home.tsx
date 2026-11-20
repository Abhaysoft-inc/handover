import { ArrowRight, Briefcase, FilePen, FolderTree, Inbox, Languages, LoaderCircle, MessageSquareText, Newspaper, Play, RotateCcw, ScanText, ShieldAlert, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { post, type DocRow, type Issue, type Stage } from "../api";
import { useAppData, useToast } from "../context";
import { DriveMap, RunFeed, type MapDoc } from "../feed";
import { navigate, useApi, useLatestRunId, useRun } from "../live";
import { Badge, Button, Card, Markdown, SectionTitle, cx, fmtDate } from "../ui";

const STAGE_ICON = { ingest: FolderTree, analyze: ScanText, review: Briefcase, draft: FilePen, execute: Inbox, knowledge: MessageSquareText };
const STAGE_ROUTE: Record<string, string> = { ingest: "", analyze: "", review: "portfolio", draft: "drafts", execute: "execution", knowledge: "ask" };

export function Stepper({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      {stages.map((s, i) => {
        const Icon = STAGE_ICON[s.key as keyof typeof STAGE_ICON] ?? Sparkles;
        return (
          <li key={s.key}>
            <button
              type="button"
              onClick={() => navigate(STAGE_ROUTE[s.key] ?? "")}
              className={cx(
                "group relative flex h-full w-full flex-col items-start rounded-xl border px-3.5 py-3 text-left transition",
                s.status === "done" && "border-emerald-200 bg-white hover:border-emerald-300",
                s.status === "active" && "border-brand bg-brand-50/60 ring-1 ring-brand/30",
                s.status === "running" && "border-brand bg-white ring-2 ring-brand/40",
                s.status === "todo" && "border-line bg-white/60 hover:bg-white",
              )}
            >
              <div className="flex w-full items-center gap-2">
                <span
                  className={cx(
                    "flex size-6 items-center justify-center rounded-md text-[11px] font-semibold",
                    s.status === "done" ? "bg-emerald-600 text-white" : s.status === "todo" ? "bg-slate-200 text-slate-600" : "bg-brand text-white",
                  )}
                >
                  {s.status === "running" ? <LoaderCircle className="size-3.5 animate-spin" /> : i + 1}
                </span>
                <Icon className={cx("size-4", s.status === "todo" ? "text-slate-400" : "text-ink-800")} />
                <span className="text-[13.5px] font-semibold text-ink-950">{s.title}</span>
              </div>
              <div className="mt-1.5 text-[12px] leading-snug text-muted">{s.status === "running" ? "Agent working…" : s.detail}</div>
              {s.status === "active" && <span className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-brand">Next step</span>}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function Home() {
  const { status, overview } = useAppData();
  const toast = useToast();
  const runId = useLatestRunId("discovery");
  const run = useRun(runId);
  const docs = useApi<DocRow[]>("/api/documents", ["documents"]);
  const issues = useApi<Issue[]>("/api/issues", ["issues"]);
  const [starting, setStarting] = useState(false);
  const running = run?.status === "running";
  const hasData = (overview?.documents.total ?? 0) > 0;
  const first = status?.counsel.name.split(" ")[0] ?? "Counsel";

  const mapDocs: MapDoc[] = useMemo(() => {
    const byId = new Map<string, MapDoc>();
    for (const d of docs.data ?? [])
      byId.set(d.id, { id: d.id, name: d.name, path: d.path, status: d.status, employment: !!d.is_employment, scanned: !!d.is_scanned });
    if (run)
      for (const d of Object.values(run.docs)) {
        const prev = byId.get(d.id);
        byId.set(d.id, { ...prev, id: d.id, name: d.name, path: d.path, status: d.status === "discovered" && prev ? prev.status : d.status, employment: d.employment ?? prev?.employment, scanned: d.scanned ?? prev?.scanned });
      }
    return [...byId.values()];
  }, [docs.data, run]);

  async function start() {
    setStarting(true);
    try {
      await post("/api/runs/discovery");
    } catch (e) {
      toast({ tone: "error", title: "Could not start", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setStarting(false);
    }
  }

  const urgent = (overview?.contracts.urgency.missed ?? 0) + (overview?.contracts.urgency.critical ?? 0);

  return (
    <div className="mx-auto max-w-[1360px] px-8 py-7">
      {/* hero */}
      <div className="relative overflow-hidden rounded-2xl bg-ink-950 px-8 py-7 text-white shadow-[0_10px_30px_-12px_rgba(11,18,32,0.45)]">
        <div className="pointer-events-none absolute -right-16 -top-24 size-[340px] rounded-full bg-brand/30 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-28 right-40 size-[260px] rounded-full bg-brass/15 blur-3xl" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.14em] text-brass">
              {status ? `${status.company} · ${fmtDate(status.today)}` : " "}
            </div>
            {running && !(overview?.contracts.total ?? 0) ? (
              <>
                <h1 className="text-[30px] font-semibold leading-tight tracking-tight">Reading {status?.company}'s Drive…</h1>
                <p className="mt-3 text-[15px] leading-relaxed text-slate-300">
                  {mapDocs.length} files found so far. The orchestrator is mapping folders, setting aside what's irrelevant, and handing documents to
                  analyst sub-agents that read them in parallel, scans and all.
                </p>
              </>
            ) : !hasData ? (
              <>
                <h1 className="text-[30px] font-semibold leading-tight tracking-tight">Welcome, {first}. Here's the handover you never got.</h1>
                <p className="mt-3 text-[15px] leading-relaxed text-slate-300">
                  Your predecessor left a Drive full of employment contracts in five languages, some of them only as scans. Handover's agents will find every
                  one, read it, reconcile the amendments, and show you which deadlines you can't miss. Then they draft, send and file the paperwork once you
                  approve it.
                </p>
              </>
            ) : (
              <>
                <h1 className="text-[30px] font-semibold leading-tight tracking-tight">
                  {overview?.contracts.total} contracts mapped. {urgent > 0 ? `${urgent} need you this week.` : "Nothing urgent."}
                </h1>
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[14px] text-slate-300">
                  <span className="inline-flex items-center gap-1.5">
                    <ScanText className="size-4 text-slate-400" /> {overview?.documents.analyzed} documents read · {overview?.documents.scanned} scanned
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Languages className="size-4 text-slate-400" /> {overview?.documents.languages.length} languages · {overview?.contracts.countries.length} countries
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <ShieldAlert className="size-4 text-slate-400" /> {overview?.risks.high} high-severity risks
                  </span>
                </div>
              </>
            )}
          </div>
          <div className="flex flex-col items-end gap-2.5">
            <div className="flex gap-2">
              {hasData && !running && (
                <Button variant="secondary" className="border-white/15 !bg-white/10 !text-white hover:!bg-white/15" icon={<ArrowRight className="size-4" />} onClick={() => navigate("portfolio")}>
                  Open portfolio
                </Button>
              )}
              <Button
                variant="primary"
                className="!bg-brass !text-ink-950 hover:!bg-amber-300"
                busy={starting || running}
                icon={hasData ? <RotateCcw className="size-4" /> : <Play className="size-4" />}
                onClick={() => void start()}
              >
                {running ? "Agents at work…" : hasData ? "Re-run discovery" : "Start discovery"}
              </Button>
            </div>
            <div className="text-right text-[12px] text-slate-400">
              {status?.mode === "live" ? (
                <>
                  Live: <span className="text-slate-200">{status.model}</span> reads every document natively
                </>
              ) : (
                <>Simulation mode: add a Claude, Groq or Gemini key to .env for live agents</>
              )}
              <br />
              Source: <span className="text-slate-200">{status?.drive.label}</span>
            </div>
          </div>
        </div>
      </div>

      {overview && (
        <div className="mt-6">
          <Stepper stages={overview.stages} />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="min-w-0">
          {run ? (
            <RunFeed run={run} maxHeight="640px" />
          ) : (
            <div className="px-6 py-10">
              <SectionTitle>Agent activity</SectionTitle>
              <p className="max-w-xl text-sm leading-relaxed text-muted">
                When you start discovery you'll watch the orchestrator explore the Drive, set aside what's irrelevant, and send documents to analyst
                sub-agents in parallel. Every tool call and progress note streams here as it happens.
              </p>
            </div>
          )}
        </Card>
        <div className="space-y-6">
          <Card className="px-4 py-4">
            <SectionTitle aside={<span className="text-xs text-muted">{mapDocs.length} files</span>}>Drive map</SectionTitle>
            {mapDocs.length ? (
              <div className="max-h-[560px] overflow-y-auto pr-1">
                <DriveMap
                  docs={mapDocs}
                  onOpen={(d) => {
                    if (d.status === "analyzed") window.open(`/api/documents/${d.id}/file`, "_blank");
                  }}
                />
              </div>
            ) : (
              <p className="text-sm text-muted">Files appear here as the agent finds them.</p>
            )}
          </Card>
        </div>
      </div>

      {overview?.briefing && (
        <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Card className="px-7 py-6">
            <div className="mb-4 flex items-center gap-2.5">
              <div className="flex size-8 items-center justify-center rounded-lg bg-ink-950 text-brass">
                <Newspaper className="size-4" />
              </div>
              <div>
                <div className="font-serif text-[18px] font-semibold text-ink-950">Your handover memo</div>
                <div className="text-xs text-muted">Written by the discovery agent · {new Date(overview.briefing.created_at).toLocaleString()}</div>
              </div>
            </div>
            <Markdown text={overview.briefing.markdown} />
          </Card>
          <Card className="px-4 py-4">
            <SectionTitle>Flagged for you</SectionTitle>
            <div className="space-y-2.5">
              {(issues.data ?? []).map((i) => (
                <div key={i.id} className="rounded-lg border border-line px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <Badge tone={i.severity === "high" ? "red" : i.severity === "medium" ? "amber" : "slate"}>{i.severity}</Badge>
                    <span className="text-[13px] font-medium text-ink-950">{i.title}</span>
                  </div>
                  <p className="mt-1 text-[12.5px] leading-snug text-muted">{i.detail}</p>
                </div>
              ))}
              {!issues.data?.length && <p className="text-sm text-muted">No cross-document issues.</p>}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
