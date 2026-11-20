import { ArrowRight, Briefcase, CalendarClock, ExternalLink, FileText, Image, PenLine, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, post, type Action, type ContractDetail, type ContractSummary, type Urgency } from "../api";
import { useAppData, useToast } from "../context";
import { navigate, useApi, useLatestRunId, useRun } from "../live";
import { RunFeed } from "../feed";
import {
  ACTION_LABEL,
  Badge,
  Button,
  Card,
  CONTRACT_TYPE,
  COUNTRY,
  CountryChip,
  Empty,
  RiskDots,
  SectionTitle,
  Spinner,
  Stat,
  Tabs,
  URGENCY_COLOR,
  URGENCY_LABEL,
  UrgencyBadge,
  cx,
  daysLabel,
  fmtDate,
} from "../ui";

// ── deadline radar ────────────────────────────────────────────────────────

function DeadlineRadar({ contracts, today }: { contracts: ContractSummary[]; today: string }) {
  const t0 = new Date(today + "T00:00:00").getTime();
  const day = 86_400_000;
  const dated = contracts.filter((c) => c.action_deadline).sort((a, b) => a.action_deadline!.localeCompare(b.action_deadline!));
  const minDays = Math.min(-10, ...dated.map((c) => c.days_left ?? 0)) - 4;
  const maxDays = Math.max(120, ...dated.map((c) => (c.days_left ?? 0) + 12));
  const pct = (d: number) => ((d - minDays) / (maxDays - minDays)) * 100;
  // lanes keep labels from colliding
  const lanes: number[] = [];
  const placed = dated.map((c) => {
    const x = pct(c.days_left ?? 0);
    let lane = 0;
    while (lanes[lane] !== undefined && x - lanes[lane] < 15) lane++;
    lanes[lane] = x;
    return { c, x, lane };
  });
  const laneCount = Math.max(1, lanes.length);
  const months: { x: number; label: string }[] = [];
  const start = new Date(t0 + minDays * day);
  const cursor = new Date(start.getFullYear(), start.getMonth() + 1, 1);
  while (cursor.getTime() < t0 + maxDays * day) {
    months.push({ x: pct((cursor.getTime() - t0) / day), label: cursor.toLocaleString("en", { month: "short" }) });
    cursor.setMonth(cursor.getMonth() + 1);
  }
  const height = 34 + laneCount * 34;
  return (
    <div className="relative select-none" style={{ height }}>
      {/* zones */}
      <div className="absolute inset-y-0 rounded-l-md bg-rose-50" style={{ left: 0, width: `${pct(0)}%` }} />
      <div className="absolute inset-y-0 bg-red-50/60" style={{ left: `${pct(0)}%`, width: `${pct(14) - pct(0)}%` }} />
      <div className="absolute inset-y-0 bg-amber-50/50" style={{ left: `${pct(14)}%`, width: `${pct(45) - pct(14)}%` }} />
      {months.map((m) => (
        <div key={m.label + m.x} className="absolute inset-y-0 border-l border-dashed border-slate-200" style={{ left: `${m.x}%` }}>
          <span className="absolute bottom-1 left-1 text-[10.5px] font-medium text-slate-400">{m.label}</span>
        </div>
      ))}
      <div className="absolute inset-y-0 w-px bg-ink-950" style={{ left: `${pct(0)}%` }}>
        <span className="absolute -top-0.5 left-1.5 rounded bg-ink-950 px-1.5 py-[1px] text-[10px] font-semibold text-white">Today</span>
      </div>
      {placed.map(({ c, x, lane }) => {
        const flip = x > 78; // keep labels near the right edge inside the chart
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => navigate(`portfolio/${c.id}`)}
            className={cx("group absolute flex items-center gap-1.5 whitespace-nowrap", flip ? "flex-row-reverse" : "")}
            style={{ left: `${x}%`, top: 22 + lane * 34, transform: flip ? "translateX(calc(-100% + 5px))" : "translateX(-5px)" }}
            title={`${c.employee_name}: ${c.action_deadline_reason ?? ""}`}
          >
            <span className="size-2.5 shrink-0 rounded-full ring-2 ring-white" style={{ background: URGENCY_COLOR[c.urgency] }} />
            <span className="rounded-md bg-white/90 px-1.5 py-0.5 text-[11.5px] font-medium text-ink-900 shadow-sm ring-1 ring-line group-hover:ring-brand">
              {c.employee_name.split(" (")[0]} <span className="tabular text-muted">· {fmtDate(c.action_deadline, false)}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ── decision form ─────────────────────────────────────────────────────────

const ACTIONS: { value: Action; hint: string }[] = [
  { value: "renew", hint: "Extend with an addendum" },
  { value: "non_renew", hint: "Let it end; give notice" },
  { value: "terminate", hint: "End early with notice" },
  { value: "convert_to_permanent", hint: "Remove the fixed term" },
  { value: "amend", hint: "Change specific clauses" },
  { value: "confirm_probation", hint: "Confirm after probation" },
  { value: "no_action", hint: "Nothing to send" },
];

function DecisionForm({ c, onSaved }: { c: ContractDetail; onSaved: () => void }) {
  const toast = useToast();
  const recommended = c.recommended_action && c.recommended_action !== "review" ? c.recommended_action : null;
  const [action, setAction] = useState<Action | null>(c.decision?.action ?? recommended);
  const [params, setParams] = useState<Record<string, string>>(c.decision?.params ?? {});
  const [busy, setBusy] = useState(false);
  const locked = c.decision?.status === "executed";
  const set = (k: string, v: string) => setParams((p) => ({ ...p, [k]: v }));

  async function save() {
    if (!action) return;
    if (action === "amend" && !params.changes) {
      toast({ tone: "error", title: "Describe the change", body: "Tell Handover what the amendment should do." });
      return;
    }
    setBusy(true);
    try {
      await post(`/api/contracts/${c.id}/decision`, { action, params });
      toast({ tone: "success", title: `Decision saved: ${ACTION_LABEL[action]}`, body: action === "no_action" ? "Nothing will be drafted." : "Handover will draft the documents in the next drafting run." });
      onSaved();
    } catch (e) {
      toast({ tone: "error", title: "Could not save", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    try {
      await api(`/api/contracts/${c.id}/decision`, { method: "DELETE" });
      setAction(recommended);
      setParams({});
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  const field = (k: string, label: string, type = "text", placeholder = "") => (
    <label className="block">
      <span className="text-[12px] font-medium text-muted">{label}</span>
      {type === "textarea" ? (
        <textarea value={params[k] ?? ""} onChange={(e) => set(k, e.target.value)} placeholder={placeholder} rows={3} disabled={locked}
          className="mt-1 w-full rounded-lg border border-line px-3 py-2 text-[13px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
      ) : (
        <input type={type} value={params[k] ?? ""} onChange={(e) => set(k, e.target.value)} placeholder={placeholder} disabled={locked}
          className="mt-1 w-full rounded-lg border border-line px-3 py-2 text-[13px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
      )}
    </label>
  );

  return (
    <div>
      <div className="grid grid-cols-2 gap-1.5">
        {ACTIONS.map((a) => (
          <button
            key={a.value}
            type="button"
            disabled={locked}
            onClick={() => setAction(a.value)}
            className={cx(
              "relative rounded-lg border px-3 py-2 text-left transition disabled:opacity-60",
              action === a.value ? "border-brand bg-brand-50 ring-1 ring-brand/40" : "border-line bg-white hover:border-slate-300",
            )}
          >
            <div className="flex items-center gap-1.5 text-[13px] font-semibold text-ink-950">
              {ACTION_LABEL[a.value]}
              {recommended === a.value && (
                <span className="inline-flex items-center gap-0.5 rounded bg-ink-950 px-1 py-[1px] text-[9.5px] font-semibold uppercase tracking-wide text-brass">
                  <Sparkles className="size-2.5" /> AI pick
                </span>
              )}
            </div>
            <div className="text-[11.5px] text-muted">{a.hint}</div>
          </button>
        ))}
      </div>
      {action && action !== "no_action" && (
        <div className="mt-3 space-y-2.5">
          {action === "renew" && (
            <div className="grid grid-cols-2 gap-2.5">
              {field("new_end_date", "New end date", "date")}
              {field("new_salary", "New pay (optional)", "text", "e.g. $280,000")}
            </div>
          )}
          {action === "terminate" && (
            <div className="grid grid-cols-2 gap-2.5">
              {field("effective_date", "Effective date (optional)", "date")}
              {field("reason", "Reason", "text", "e.g. restructuring")}
            </div>
          )}
          {action === "convert_to_permanent" && field("effective_date", "Effective date (optional)", "date")}
          {action === "amend" && field("changes", "What should change?", "textarea", "e.g. Delete Section 4 (Non-Competition) and add the §16600.1 notice.")}
          {field("notes", "Instructions for the drafter (optional)", "textarea", "Tone, extra clauses, who signs…")}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button variant="primary" busy={busy} disabled={!action || locked} onClick={() => void save()}>
          {c.decision ? "Update decision" : "Save decision"}
        </Button>
        {c.decision && !locked && (
          <Button variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => void clear()}>
            Clear
          </Button>
        )}
        {locked && <Badge tone="green">Executed — locked</Badge>}
      </div>
    </div>
  );
}

// ── contract drawer ───────────────────────────────────────────────────────

function Drawer({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useApi<ContractDetail>(`/api/contracts/${id}`, ["contracts", "drafts", "reminders"]);
  const [clauseLang, setClauseLang] = useState<"en" | "orig">("en");
  const c = detail.data;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink-950/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="animate-rise relative flex h-full w-[620px] max-w-[94vw] flex-col bg-canvas shadow-2xl">
        {!c ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <>
            <div className="border-b border-line bg-white px-6 py-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <CountryChip code={c.country} />
                    <span className="text-xs font-medium text-muted">{c.id}</span>
                    {c.language_name && <Badge>{c.language_name}</Badge>}
                    {c.primary_document?.is_scanned ? <Badge tone="violet">from scan</Badge> : null}
                  </div>
                  <h2 className="mt-2 text-[22px] font-semibold tracking-tight text-ink-950">{c.employee_name}</h2>
                  <div className="text-[13.5px] text-muted">
                    {c.job_title} · {c.employer_entity}
                  </div>
                </div>
                <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                  <X className="size-5" />
                </button>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-3 text-[12.5px]">
                <div>
                  <div className="text-muted">Contract</div>
                  <div className="font-medium text-ink-950">{CONTRACT_TYPE[c.contract_type ?? ""] ?? c.contract_type}{c.auto_renewal ? " · auto-renews" : ""}</div>
                </div>
                <div>
                  <div className="text-muted">Term</div>
                  <div className="font-medium text-ink-950">
                    {fmtDate(c.start_date)} → {c.end_date ? fmtDate(c.end_date) : "open-ended"}
                  </div>
                </div>
                <div>
                  <div className="text-muted">Pay</div>
                  <div className="font-medium text-ink-950">{c.compensation_text ?? "—"}</div>
                </div>
              </div>
            </div>

            <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
              <Card className="overflow-hidden">
                <div className="flex items-center gap-3 px-4 py-3" style={{ background: `${URGENCY_COLOR[c.urgency]}12` }}>
                  <CalendarClock className="size-5" style={{ color: URGENCY_COLOR[c.urgency] }} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] font-semibold text-ink-950">
                      {c.action_deadline ? `Act by ${fmtDate(c.action_deadline)}` : "No deadline"}{" "}
                      <span className="font-normal text-muted">· {c.action_deadline ? daysLabel(c.days_left) : URGENCY_LABEL[c.urgency]}</span>
                    </div>
                    {c.action_deadline_reason && <div className="text-[12.5px] text-ink-800">{c.action_deadline_reason}</div>}
                  </div>
                  <UrgencyBadge urgency={c.urgency} days={c.days_left} />
                </div>
                {c.status_note && <div className="border-t border-line px-4 py-2 text-[12.5px] text-ink-800">{c.status_note}</div>}
              </Card>

              <div>
                <SectionTitle>What Handover found</SectionTitle>
                <p className="text-[13.5px] leading-relaxed text-ink-800">{c.summary}</p>
              </div>

              <Card className="px-4 py-4">
                <div className="mb-3 flex items-start gap-2.5">
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-ink-950 text-brass">
                    <Sparkles className="size-3.5" />
                  </div>
                  <div>
                    <div className="text-[13.5px] font-semibold text-ink-950">
                      Recommendation: {ACTION_LABEL[c.recommended_action ?? "review"]}
                    </div>
                    <div className="text-[12.5px] leading-snug text-muted">{c.recommendation_rationale}</div>
                  </div>
                </div>
                <div className="border-t border-line pt-3">
                  <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted">Your decision</div>
                  <DecisionForm key={`${c.id}-${c.decision?.action}-${c.decision?.status}`} c={c} onSaved={() => void detail.reload()} />
                </div>
              </Card>

              {c.risk_flags.length > 0 && (
                <div>
                  <SectionTitle>Risks</SectionTitle>
                  <div className="space-y-2">
                    {c.risk_flags.map((r) => (
                      <div key={r.title} className="rounded-lg border border-line bg-white px-3.5 py-2.5">
                        <div className="flex items-center gap-2">
                          <Badge tone={r.severity === "high" ? "red" : r.severity === "medium" ? "amber" : "slate"}>{r.severity}</Badge>
                          <span className="text-[13px] font-semibold text-ink-950">{r.title}</span>
                        </div>
                        <p className="mt-1 text-[12.5px] leading-snug text-ink-800">{r.detail}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {c.key_clauses.length > 0 && (
                <div>
                  <SectionTitle
                    aside={
                      c.language && c.language !== "en" ? (
                        <Tabs value={clauseLang} onChange={setClauseLang} items={[{ value: "en", label: "English" }, { value: "orig", label: c.language_name ?? "Original" }]} />
                      ) : undefined
                    }
                  >
                    Key clauses
                  </SectionTitle>
                  <div className="space-y-2">
                    {c.key_clauses.map((k) => (
                      <div key={k.heading} className="rounded-lg border border-line bg-white px-3.5 py-2.5">
                        <div className="flex items-center justify-between text-[12px] font-semibold text-ink-950">
                          {k.heading}
                          {k.page > 0 && <span className="font-normal text-muted">p. {k.page}</span>}
                        </div>
                        <p className="mt-1 font-serif text-[14px] leading-relaxed text-ink-800">“{clauseLang === "en" ? k.english_text : k.original_text}”</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {c.key_dates.length > 0 && (
                <div>
                  <SectionTitle>Key dates</SectionTitle>
                  <div className="space-y-1">
                    {c.key_dates.map((k) => (
                      <div key={k.label} className="flex items-baseline gap-3 text-[13px]">
                        <span className="w-28 shrink-0 font-medium tabular text-ink-950">{fmtDate(k.date)}</span>
                        <span className="text-ink-800">{k.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <SectionTitle>Source documents</SectionTitle>
                <div className="space-y-1.5">
                  {c.documents.filter(Boolean).map((d) => (
                    <a
                      key={d.id}
                      href={`/api/documents/${d.id}/file`}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-2.5 rounded-lg border border-line bg-white px-3 py-2 hover:border-brand"
                    >
                      {d.is_scanned ? <Image className="size-4 text-violet-600" /> : <FileText className="size-4 text-slate-500" />}
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px] font-medium text-ink-950">{d.name}</div>
                        <div className="truncate text-[11.5px] text-muted">
                          {d.path} · {d.kind?.replace(/_/g, " ")} · {d.language_name}
                          {d.is_scanned ? " · image-only scan" : ""}
                        </div>
                      </div>
                      <ExternalLink className="size-3.5 text-slate-400" />
                    </a>
                  ))}
                </div>
              </div>

              {c.activity.length > 0 && (
                <div>
                  <SectionTitle>Timeline</SectionTitle>
                  <ol className="space-y-2 border-l border-line pl-4">
                    {c.activity.map((a, i) => (
                      <li key={i} className="relative text-[12.5px]">
                        <span className="absolute -left-[21px] top-1 size-2 rounded-full bg-slate-300 ring-2 ring-canvas" />
                        <span className="text-ink-800">{a.text}</span>
                        <span className="ml-2 text-[11px] text-muted">{new Date(a.ts).toLocaleString()}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── page ──────────────────────────────────────────────────────────────────

const FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "All" },
  { value: "urgent", label: "Needs action" },
  { value: "undecided", label: "Undecided" },
  { value: "decided", label: "Decided" },
];

export function Portfolio({ contractId }: { contractId?: string }) {
  const { status, overview } = useAppData();
  const toast = useToast();
  const contracts = useApi<ContractSummary[]>("/api/contracts", ["contracts"]);
  const draftRunId = useLatestRunId("drafting");
  const draftRun = useRun(draftRunId);
  const [filter, setFilter] = useState("all");
  const [starting, setStarting] = useState(false);
  const rows = useMemo(() => {
    const all = contracts.data ?? [];
    if (filter === "urgent") return all.filter((c) => (["missed", "critical", "soon"] as Urgency[]).includes(c.urgency) || c.risks.high > 0);
    if (filter === "undecided") return all.filter((c) => !c.decision);
    if (filter === "decided") return all.filter((c) => c.decision);
    return all;
  }, [contracts.data, filter]);
  const pending = (contracts.data ?? []).filter((c) => c.decision?.status === "pending_draft");
  const urgent = (overview?.contracts.urgency.missed ?? 0) + (overview?.contracts.urgency.critical ?? 0);

  async function draftAll() {
    setStarting(true);
    try {
      await post("/api/runs/drafting");
      navigate("drafts");
    } catch (e) {
      toast({ tone: "error", title: "Could not start drafting", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setStarting(false);
    }
  }

  if (contracts.data && contracts.data.length === 0)
    return (
      <div className="mx-auto max-w-[1360px] px-8 py-7">
        <Card>
          <Empty icon={<Briefcase className="size-6" />} title="No contracts yet">
            Run discovery from Mission control. Handover will find, read and register every employment contract.
            <div className="mt-4">
              <Button variant="primary" onClick={() => navigate("")}>
                Go to Mission control
              </Button>
            </div>
          </Empty>
        </Card>
      </div>
    );

  return (
    <div className="mx-auto max-w-[1360px] px-8 py-7 pb-28">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-muted">Step 3 · Review</div>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight text-ink-950">Contract portfolio</h1>
          <p className="mt-1 text-[14px] text-muted">Every worker, every deadline. Assign an action and Handover drafts the paperwork.</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Contracts" value={overview?.contracts.total ?? "—"} hint={`${overview?.contracts.countries.length ?? 0} countries · ${overview?.documents.languages.length ?? 0} languages`} />
        <Stat label="Act within 14 days" value={urgent} tone={urgent ? "text-red-600" : ""} hint={`${overview?.contracts.urgency.missed ?? 0} already missed`} />
        <Stat label="Next 45 days" value={(overview?.contracts.urgency.soon ?? 0) + urgent} hint="including the urgent ones" />
        <Stat label="High-severity risks" value={overview?.risks.high ?? 0} tone={overview?.risks.high ? "text-amber-700" : ""} hint={`${overview?.risks.medium ?? 0} medium`} />
      </div>

      <Card className="mt-5 px-5 pb-4 pt-4">
        <SectionTitle aside={<span className="text-xs text-muted">Click a name to open the contract</span>}>Deadline radar</SectionTitle>
        {contracts.data && status ? <DeadlineRadar contracts={contracts.data} today={status.today} /> : <Spinner />}
      </Card>

      {draftRun?.status === "running" && (
        <Card className="mt-5">
          <RunFeed run={draftRun} maxHeight="260px" />
        </Card>
      )}

      <Card className="mt-5 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <Tabs value={filter} onChange={setFilter} items={FILTERS} />
          <span className="text-xs text-muted">{rows.length} shown</span>
        </div>
        <table className="w-full text-left text-[13px]">
          <thead className="bg-slate-50/80 text-[11.5px] font-semibold uppercase tracking-wide text-muted">
            <tr>
              <th className="px-5 py-2.5">Worker</th>
              <th className="px-3 py-2.5">Contract</th>
              <th className="px-3 py-2.5">Next deadline</th>
              <th className="px-3 py-2.5">Risks</th>
              <th className="px-3 py-2.5">Handover suggests</th>
              <th className="px-5 py-2.5 text-right">Decision</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((c) => (
              <tr key={c.id} onClick={() => navigate(`portfolio/${c.id}`)} className="cursor-pointer align-top transition hover:bg-slate-50/70">
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <CountryChip code={c.country} />
                    <span className="font-semibold text-ink-950">{c.employee_name}</span>
                  </div>
                  <div className="mt-0.5 text-[12px] text-muted">
                    {c.job_title}
                    {c.language_name && c.language_name !== "English" ? ` · ${c.language_name}` : ""}
                    {c.primary_document?.is_scanned ? " · scan" : ""}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <div className="text-ink-900">{CONTRACT_TYPE[c.contract_type ?? ""] ?? "—"}</div>
                  <div className="text-[12px] text-muted">{c.end_date ? `ends ${fmtDate(c.end_date)}` : COUNTRY[c.country ?? ""] ?? ""}</div>
                </td>
                <td className="max-w-[300px] px-3 py-3">
                  <div className="flex items-center gap-2">
                    <span className="tabular font-medium text-ink-950">{c.action_deadline ? fmtDate(c.action_deadline) : "—"}</span>
                    <UrgencyBadge urgency={c.urgency} days={c.days_left} />
                  </div>
                  {c.action_deadline_reason && <div className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-muted">{c.action_deadline_reason}</div>}
                </td>
                <td className="px-3 py-3">
                  <RiskDots risks={c.risks} />
                </td>
                <td className="px-3 py-3">
                  <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-ink-800">
                    <Sparkles className="size-3.5 text-amber-500" />
                    {ACTION_LABEL[c.recommended_action ?? "review"]}
                  </span>
                </td>
                <td className="px-5 py-3 text-right">
                  {c.decision ? (
                    <div className="inline-flex flex-col items-end gap-1">
                      <Badge tone="blue">{ACTION_LABEL[c.decision.action]}</Badge>
                      <span className="text-[11px] text-muted">{c.decision.status.replace(/_/g, " ")}</span>
                    </div>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-brand">
                      Decide <ArrowRight className="size-3.5" />
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {pending.length > 0 && (
        <div className="fixed bottom-5 left-[calc(244px+50%)] z-30 -translate-x-1/2">
          <div className="animate-rise flex items-center gap-4 rounded-2xl bg-ink-950 py-2.5 pl-5 pr-2.5 text-white shadow-2xl">
            <span className="text-[13.5px]">
              <span className="font-semibold">{pending.length} decision{pending.length > 1 ? "s" : ""}</span> ready. Handover will draft each document in
              the contract's own language.
            </span>
            <Button variant="primary" className="!bg-brass !text-ink-950 hover:!bg-amber-300" busy={starting || draftRun?.status === "running"} icon={<PenLine className="size-4" />} onClick={() => void draftAll()}>
              Draft documents
            </Button>
          </div>
        </div>
      )}

      {contractId && <Drawer id={contractId} onClose={() => navigate("portfolio")} />}
    </div>
  );
}
