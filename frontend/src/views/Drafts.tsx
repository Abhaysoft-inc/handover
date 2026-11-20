import { Check, Download, FilePen, Mail, Pencil, Send, ShieldCheck, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, post, type Draft } from "../api";
import { useToast } from "../context";
import { RunFeed } from "../feed";
import { navigate, useApi, useLatestRunId, useRun } from "../live";
import { Badge, Button, Card, CountryChip, Empty, Markdown, SectionTitle, Spinner, Tabs, cx, fmtDate } from "../ui";

const STATUS_TONE: Record<string, string> = { draft: "amber", approved: "blue", sent: "sky", filed: "sky", executed: "green" };
const STATUS_LABEL: Record<string, string> = { draft: "Needs review", approved: "Approved", sent: "Sent", filed: "Filed", executed: "Executed" };
const METHOD_LABEL: Record<string, string> = {
  email: "Email",
  email_and_wet_ink: "Email + wet-ink original",
  wet_ink_only: "Wet-ink original only",
  e_signature: "E-signature",
};
const LANG: Record<string, string> = { en: "English", de: "German", es: "Spanish", fr: "French", ja: "Japanese", hi: "Hindi" };

function Viewer({ draft, onChanged }: { draft: Draft; onChanged: () => void }) {
  const toast = useToast();
  const [tab, setTab] = useState<"doc" | "notes" | "delivery">("doc");
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(draft.body_markdown);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setBody(draft.body_markdown);
    setEditing(false);
  }, [draft.id, draft.body_markdown]);
  const editable = draft.status === "draft" || draft.status === "approved";

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      toast({ tone: "success", title: ok });
      onChanged();
    } catch (e) {
      toast({ tone: "error", title: "Action failed", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex min-h-[640px] min-w-0 flex-col">
      <div className="border-b border-line px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <CountryChip code={draft.country} />
              <span className="text-xs font-medium text-muted">
                {draft.id} · v{draft.version} · {LANG[draft.language] ?? draft.language}
              </span>
              <Badge tone={STATUS_TONE[draft.status] ?? "slate"}>{STATUS_LABEL[draft.status] ?? draft.status}</Badge>
            </div>
            <h2 className="mt-1.5 truncate text-[19px] font-semibold tracking-tight text-ink-950">{draft.title}</h2>
            <button type="button" onClick={() => navigate(`portfolio/${draft.contract_id}`)} className="text-[13px] text-brand hover:underline">
              {draft.employee_name} · {draft.contract_id}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a href={`/api/drafts/${draft.id}/download?format=pdf`} target="_blank" rel="noreferrer">
              <Button variant="ghost" icon={<Download className="size-4" />}>PDF</Button>
            </a>
            <a href={`/api/drafts/${draft.id}/download?format=docx`}>
              <Button variant="ghost" icon={<Download className="size-4" />}>DOCX</Button>
            </a>
            {editable && !editing && (
              <Button icon={<Pencil className="size-4" />} onClick={() => { setEditing(true); setTab("doc"); }}>
                Edit
              </Button>
            )}
            {draft.status === "draft" && !editing && (
              <Button variant="primary" busy={busy} icon={<Check className="size-4" />} onClick={() => void act(() => post(`/api/drafts/${draft.id}/approve`), "Approved for execution")}>
                Approve
              </Button>
            )}
            {draft.status === "approved" && !editing && (
              <Button busy={busy} icon={<Undo2 className="size-4" />} onClick={() => void act(() => post(`/api/drafts/${draft.id}/unapprove`), "Moved back to review")}>
                Send back
              </Button>
            )}
          </div>
        </div>
        <div className="mt-3">
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { value: "doc", label: "Document" },
              { value: "notes", label: `Counsel notes${draft.compliance_notes.length ? ` (${draft.compliance_notes.length})` : ""}` },
              { value: "delivery", label: "Delivery plan" },
            ]}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-slate-50/70 px-6 py-6">
        {tab === "doc" &&
          (editing ? (
            <div>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                className="h-[520px] w-full rounded-xl border border-line bg-white p-5 font-mono text-[13px] leading-relaxed outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
              <div className="mt-3 flex gap-2">
                <Button variant="primary" busy={busy} onClick={() => void act(() => api(`/api/drafts/${draft.id}`, { method: "PUT", body: { body_markdown: body } }), "Saved — needs re-approval")}>
                  Save changes
                </Button>
                <Button variant="ghost" onClick={() => { setEditing(false); setBody(draft.body_markdown); }}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-[720px] rounded-sm bg-white px-12 py-12 shadow-[0_1px_3px_rgba(16,24,40,0.08),0_8px_24px_rgba(16,24,40,0.06)]">
              <Markdown text={draft.body_markdown} className="prose-doc" />
            </div>
          ))}
        {tab === "notes" && (
          <div className="mx-auto max-w-[720px] space-y-4">
            <Card className="px-5 py-4">
              <SectionTitle>Summary for counsel</SectionTitle>
              <p className="text-[14px] leading-relaxed text-ink-800">{draft.english_summary}</p>
            </Card>
            <Card className="px-5 py-4">
              <SectionTitle>Formalities & compliance</SectionTitle>
              {draft.compliance_notes.length ? (
                <ul className="space-y-2">
                  {draft.compliance_notes.map((n) => (
                    <li key={n} className="flex gap-2.5 text-[13.5px] leading-snug text-ink-800">
                      <ShieldCheck className="mt-0.5 size-4 shrink-0 text-amber-600" />
                      {n}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">No special formalities flagged.</p>
              )}
            </Card>
          </div>
        )}
        {tab === "delivery" && (
          <div className="mx-auto max-w-[720px] space-y-4">
            <Card className="px-5 py-4">
              <div className="grid grid-cols-2 gap-4 text-[13px]">
                <div>
                  <div className="text-muted">Method</div>
                  <div className="font-medium text-ink-950">{METHOD_LABEL[draft.delivery.method] ?? draft.delivery.method}</div>
                </div>
                <div>
                  <div className="text-muted">Sign by</div>
                  <div className="font-medium text-ink-950">{draft.delivery.sign_by_date ? fmtDate(draft.delivery.sign_by_date) : "—"}</div>
                </div>
                <div>
                  <div className="text-muted">To</div>
                  <div className="font-medium text-ink-950">{draft.delivery.to.join(", ") || "—"}</div>
                </div>
                <div>
                  <div className="text-muted">Cc</div>
                  <div className="font-medium text-ink-950">{draft.delivery.cc.join(", ") || "—"}</div>
                </div>
                <div className="col-span-2">
                  <div className="text-muted">Signatures needed</div>
                  <div className="font-medium text-ink-950">{draft.delivery.signature_required_from.join(", ") || "—"}</div>
                </div>
              </div>
            </Card>
            <Card className="px-5 py-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink-950">
                <Mail className="size-4 text-muted" /> {draft.delivery.subject}
              </div>
              <pre className="whitespace-pre-wrap font-sans text-[13.5px] leading-relaxed text-ink-800">{draft.delivery.body}</pre>
            </Card>
          </div>
        )}
      </div>
    </Card>
  );
}

export function Drafts({ draftId }: { draftId?: string }) {
  const toast = useToast();
  const drafts = useApi<Draft[]>("/api/drafts", ["drafts"]);
  const runId = useLatestRunId("drafting");
  const run = useRun(runId);
  const execRunId = useLatestRunId("execution");
  const execRun = useRun(execRunId);
  const [starting, setStarting] = useState(false);
  const list = drafts.data ?? [];
  const selected = list.find((d) => d.id === draftId) ?? list[0];
  const approved = list.filter((d) => d.status === "approved");
  const review = list.filter((d) => d.status === "draft");

  async function execute() {
    setStarting(true);
    try {
      await post("/api/runs/execution");
      navigate("execution");
    } catch (e) {
      toast({ tone: "error", title: "Could not start execution", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setStarting(false);
    }
  }

  async function approveAll() {
    for (const d of review) await post(`/api/drafts/${d.id}/approve`);
    toast({ tone: "success", title: `Approved ${review.length} document${review.length > 1 ? "s" : ""}` });
    void drafts.reload();
  }

  return (
    <div className="mx-auto max-w-[1360px] px-8 py-7">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-muted">Step 4 · Draft</div>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight text-ink-950">Drafts for your review</h1>
          <p className="mt-1 text-[14px] text-muted">Each document is grounded in the original contract and its governing law. Nothing leaves until you approve it.</p>
        </div>
        <div className="flex gap-2">
          {review.length > 1 && (
            <Button icon={<Check className="size-4" />} onClick={() => void approveAll()}>
              Approve all {review.length}
            </Button>
          )}
          <Button variant="primary" busy={starting || execRun?.status === "running"} disabled={!approved.length} icon={<Send className="size-4" />} onClick={() => void execute()}>
            Execute {approved.length || ""} approved
          </Button>
        </div>
      </div>

      {run?.status === "running" && (
        <Card className="mb-5">
          <RunFeed run={run} maxHeight="300px" />
        </Card>
      )}

      {drafts.data && list.length === 0 && run?.status !== "running" ? (
        <Card>
          <Empty icon={<FilePen className="size-6" />} title="No drafts yet">
            Assign actions on the Portfolio, then press <b>Draft documents</b>. Handover writes addenda, notices and letters in each contract's language.
            <div className="mt-4">
              <Button variant="primary" onClick={() => navigate("portfolio")}>
                Go to Portfolio
              </Button>
            </div>
          </Empty>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
          <div className="space-y-2">
            {!drafts.data && <Spinner />}
            {list.map((d) => (
              <button
                key={d.id}
                type="button"
                onClick={() => navigate(`drafts/${d.id}`)}
                className={cx(
                  "w-full rounded-xl border bg-white px-4 py-3 text-left transition",
                  selected?.id === d.id ? "border-brand ring-1 ring-brand/30" : "border-line hover:border-slate-300",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13.5px] font-semibold text-ink-950">{d.employee_name}</span>
                  <Badge tone={STATUS_TONE[d.status] ?? "slate"}>{STATUS_LABEL[d.status] ?? d.status}</Badge>
                </div>
                <div className="mt-0.5 truncate text-[12.5px] text-ink-800">{d.title}</div>
                <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-muted">
                  <CountryChip code={d.country} /> {LANG[d.language] ?? d.language}
                  {d.compliance_notes.length > 0 && <span>· {d.compliance_notes.length} formalities</span>}
                </div>
              </button>
            ))}
          </div>
          {selected ? <Viewer draft={selected} onChanged={() => void drafts.reload()} /> : <div />}
        </div>
      )}
    </div>
  );
}
