import { AlarmClock, BellRing, ChevronDown, FastForward, FileText, FolderOpen, Inbox, Mail, Paperclip } from "lucide-react";
import { useState } from "react";
import { post, type Email, type Filing, type Reminder } from "../api";
import { useAppData, useToast } from "../context";
import { RunFeed } from "../feed";
import { navigate, useApi, useLatestRunId, useRun } from "../live";
import { Badge, Button, Card, Empty, SectionTitle, cx, fmtDate } from "../ui";

const KIND_TONE: Record<string, string> = { deadline: "red", signature: "blue", delivery: "amber", follow_up: "sky" };

function EmailRow({ e }: { e: Email }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-line last:border-0">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 px-5 py-3 text-left hover:bg-slate-50/70">
        <div className={cx("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg", e.kind === "reminder" ? "bg-amber-50 text-amber-700" : "bg-brand-50 text-brand")}>
          {e.kind === "reminder" ? <BellRing className="size-3.5" /> : <Mail className="size-3.5" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[13.5px] font-semibold text-ink-950">{e.subject}</span>
            {e.attachments.length > 0 && (
              <span className="inline-flex items-center gap-0.5 text-[11px] text-muted">
                <Paperclip className="size-3" /> {e.attachments.length}
              </span>
            )}
          </div>
          <div className="truncate text-[12px] text-muted">
            to {e.to.join(", ")}
            {e.cc.length ? ` · cc ${e.cc.join(", ")}` : ""}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge tone={e.transport === "smtp" ? "green" : "violet"}>{e.transport === "smtp" ? "delivered" : "sandbox"}</Badge>
          <span className="text-[11px] text-muted">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        </div>
        <ChevronDown className={cx("mt-1 size-4 text-slate-400 transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="px-5 pb-4 pl-[60px]">
          <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 px-4 py-3 font-sans text-[13px] leading-relaxed text-ink-800">{e.body}</pre>
          {e.attachments.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {e.attachments.map((a) => (
                <span key={a} className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11.5px] text-ink-800">
                  <FileText className="size-3" /> {a}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Execution() {
  const { status } = useAppData();
  const toast = useToast();
  const outbox = useApi<Email[]>("/api/outbox", ["outbox"]);
  const reminders = useApi<Reminder[]>("/api/reminders", ["reminders"]);
  const filings = useApi<Filing[]>("/api/filings", ["filings"]);
  const runId = useLatestRunId("execution");
  const run = useRun(runId);
  const [ff, setFf] = useState<number | null>(null);
  const [tab, setTab] = useState<"all" | "document" | "reminder">("all");

  async function fastForward(days: number) {
    setFf(days);
    try {
      const r = await post<{ as_of: string; fired: unknown[] }>("/api/reminders/sweep", { days_ahead: days });
      toast({
        tone: r.fired.length ? "reminder" : "info",
        title: r.fired.length ? `${r.fired.length} reminder${r.fired.length > 1 ? "s" : ""} fired` : "Nothing due yet",
        body: `Swept everything due by ${fmtDate(r.as_of)}.`,
      });
    } finally {
      setFf(null);
    }
  }

  const emails = (outbox.data ?? []).filter((e) => tab === "all" || e.kind === tab);
  const scheduled = (reminders.data ?? []).filter((r) => r.status === "scheduled");
  const sent = (reminders.data ?? []).filter((r) => r.status === "sent");

  return (
    <div className="mx-auto max-w-[1360px] px-8 py-7">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-muted">Step 5 · Execute</div>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight text-ink-950">Sent, scheduled, filed</h1>
          <p className="mt-1 text-[14px] text-muted">
            The execution agent delivers approved documents, chases signatures, and files final versions.{" "}
            {status?.mail.transport === "sandbox" ? "Email is in sandbox mode: nothing leaves this machine." : `Email goes out via SMTP${status?.mail.redirect ? ` (redirected to ${status.mail.redirect})` : ""}.`}
          </p>
        </div>
      </div>

      {run && (
        <Card className="mb-5">
          <RunFeed run={run} maxHeight={run.status === "running" ? "420px" : "220px"} />
        </Card>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Card className="min-w-0 overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-3">
            <div className="flex items-center gap-2 text-[14px] font-semibold text-ink-950">
              <Inbox className="size-4 text-muted" /> Outbox
            </div>
            <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-[12.5px]">
              {(["all", "document", "reminder"] as const).map((t) => (
                <button key={t} type="button" onClick={() => setTab(t)} className={cx("rounded-md px-2.5 py-1 font-medium", tab === t ? "bg-white shadow-sm" : "text-muted")}>
                  {t === "all" ? "All" : t === "document" ? "Documents" : "Reminders"}
                </button>
              ))}
            </div>
          </div>
          {emails.length ? (
            emails.map((e) => <EmailRow key={e.id} e={e} />)
          ) : (
            <Empty icon={<Mail className="size-6" />} title="Nothing sent yet">
              Approve drafts, then run execution from the Drafts page.
              <div className="mt-4">
                <Button onClick={() => navigate("drafts")}>Go to Drafts</Button>
              </div>
            </Empty>
          )}
        </Card>

        <div className="space-y-5">
          <Card className="px-4 py-4">
            <SectionTitle aside={<span className="text-xs text-muted">{scheduled.length} scheduled · {sent.length} sent</span>}>Reminders</SectionTitle>
            <div className="mb-3 rounded-lg border border-dashed border-amber-300 bg-amber-50/60 px-3 py-2.5">
              <div className="flex items-center gap-2 text-[12.5px] font-semibold text-amber-900">
                <FastForward className="size-4" /> Demo: fast-forward time
              </div>
              <div className="mt-0.5 text-[12px] text-amber-900/80">Send every reminder that would be due by then.</div>
              <div className="mt-2 flex gap-1.5">
                {[3, 7, 30].map((d) => (
                  <Button key={d} variant="secondary" className="!px-2.5 !py-1 text-[12px]" busy={ff === d} onClick={() => void fastForward(d)}>
                    +{d} days
                  </Button>
                ))}
              </div>
            </div>
            <div className="max-h-[440px] space-y-1.5 overflow-y-auto pr-1">
              {(reminders.data ?? []).filter((r) => r.status !== "cancelled").map((r) => (
                <div key={r.id} className={cx("rounded-lg border px-3 py-2", r.status === "sent" ? "border-line bg-slate-50" : "border-line bg-white")}>
                  <div className="flex items-center gap-2">
                    <AlarmClock className={cx("size-3.5", r.status === "sent" ? "text-slate-400" : "text-ink-700")} />
                    <span className="tabular text-[12px] font-semibold text-ink-950">{fmtDate(r.due_date)}</span>
                    <Badge tone={KIND_TONE[r.kind] ?? "slate"}>{r.kind.replace("_", " ")}</Badge>
                    {r.status === "sent" && <Badge tone="green">sent</Badge>}
                  </div>
                  <div className={cx("mt-1 text-[12.5px] leading-snug", r.status === "sent" ? "text-muted" : "text-ink-800")}>{r.subject}</div>
                  <div className="text-[11px] text-muted">→ {r.recipients.join(", ")}</div>
                </div>
              ))}
              {!reminders.data?.length && <p className="text-sm text-muted">No reminders yet.</p>}
            </div>
          </Card>

          <Card className="px-4 py-4">
            <SectionTitle aside={<span className="text-xs text-muted">{status?.drive.source === "google" ? "Google Drive" : "Demo Drive"}</span>}>Filed final versions</SectionTitle>
            <div className="space-y-1.5">
              {(filings.data ?? []).map((f) => (
                <a key={f.id} href={`/api/filings/${f.id}/file`} target="_blank" rel="noreferrer" className="flex items-start gap-2.5 rounded-lg border border-line px-3 py-2 hover:border-brand">
                  <FolderOpen className="mt-0.5 size-4 shrink-0 text-amber-600" />
                  <div className="min-w-0">
                    <div className="truncate text-[12.5px] font-medium text-ink-950">{f.file_name}</div>
                    <div className="truncate text-[11.5px] text-muted">{f.folder_path}</div>
                  </div>
                </a>
              ))}
              {!filings.data?.length && <p className="text-sm text-muted">Nothing filed yet.</p>}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
