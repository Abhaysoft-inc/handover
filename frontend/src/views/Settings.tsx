import { Cloud, Cpu, HardDrive, Mail, RotateCcw, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { post } from "../api";
import { useAppData, useToast } from "../context";
import { Badge, Button, Card, cx } from "../ui";

function Row({ icon, title, children, aside }: { icon: ReactNode; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <Card className="flex gap-4 px-5 py-5">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-ink-800">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[15px] font-semibold text-ink-950">{title}</h3>
          {aside}
        </div>
        <div className="mt-1 text-[13.5px] leading-relaxed text-ink-800">{children}</div>
      </div>
    </Card>
  );
}

const Code = ({ children }: { children: ReactNode }) => (
  <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-ink-900">{children}</code>
);

export function Settings() {
  const { status, reloadStatus } = useAppData();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "");

  async function run(key: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(key);
    try {
      await fn();
      toast({ tone: "success", title: ok });
      reloadStatus();
    } catch (e) {
      toast({ tone: "error", title: "Failed", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  if (!status) return null;
  const google = status.drive;
  return (
    <div className="mx-auto max-w-[860px] space-y-4 px-8 py-7">
      <div className="mb-2">
        <h1 className="text-[26px] font-semibold tracking-tight text-ink-950">Settings</h1>
        <p className="mt-1 text-[14px] text-muted">Configuration lives in <Code>.env</Code> at the project root. Changes apply to the next agent run, no restart needed.</p>
      </div>

      {params.get("drive") === "connected" && <div className="rounded-lg bg-emerald-50 px-4 py-2.5 text-[13px] text-emerald-800">Google Drive connected. Discovery will now read your Drive.</div>}
      {params.get("drive_error") && <div className="rounded-lg bg-red-50 px-4 py-2.5 text-[13px] text-red-800">Google sign-in failed: {params.get("drive_error")}</div>}

      <Row
        icon={<Cpu className="size-4" />}
        title="AI model"
        aside={<Badge tone={status.mode === "live" ? "green" : "violet"}>{status.mode === "live" ? `Live · ${status.provider_label}` : "Simulation"}</Badge>}
      >
        {status.mode === "live" && status.provider === "anthropic" && (
          <>
            Agents run on Claude <b>{status.model}</b> at <b>{status.effort}</b> effort, with server-side refusal fallbacks and live progress updates.
            Every document is read natively (text and page images), so scans and non-English contracts need no OCR.
          </>
        )}
        {status.mode === "live" && status.provider === "gemini" && (
          <>
            Agents run on Gemini <b>{status.model}</b> through the same tool loop, with thought summaries as progress notes and automatic retries for
            rate limits. Gemini reads PDFs natively too, scans included. Pin a model with <Code>GEMINI_MODEL</Code>. Free-tier keys are
            rate-limited and can be slow at busy times; Handover backs off on 429s and switches to another Flash model when one is overloaded.
          </>
        )}
        {status.mode === "live" && (status.provider === "groq" || status.provider === "openai_compat") && (
          <>
            Agents run on {status.provider_label}: <b>{status.model}</b> orchestrates with parallel tool calls, document analysis is spread
            across several models (each has its own free limits), and scanned pages are read by a vision model. Requests are paced to stay
            inside the free tier's per-minute token budget. Change models with <Code>GROQ_AGENT_MODEL</Code>, <Code>GROQ_ANALYST_MODELS</Code>{" "}
            and <Code>GROQ_VISION_MODEL</Code>.
          </>
        )}
        {status.mode !== "live" && (
          <>
            No API key found, so the full workflow runs in <b>Simulation mode</b>: the real tools, database, drafts, outbox and reminders, driven by a
            scripted brain and the demo corpus. Add <Code>ANTHROPIC_API_KEY</Code> (Claude), <Code>GROQ_API_KEY</Code> (free at console.groq.com)
            or <Code>GEMINI_API_KEY</Code> (free at aistudio.google.com) to <Code>.env</Code> to switch to live agents. With several set,{" "}
            <Code>LLM_PROVIDER</Code> picks one.
          </>
        )}
      </Row>

      <Row
        icon={<HardDrive className="size-4" />}
        title="Document source"
        aside={<Badge tone={google.source === "google" ? "green" : "slate"}>{google.label}</Badge>}
      >
        <p>
          The bundled <b>Demo Drive</b> holds 15 files: typed and scanned PDFs, a DOCX, five languages and some distractors. Connect Google Drive to run
          the same workflow on real folders (read-only for discovery; Handover files only the documents it creates).
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {google.google_connected ? (
            <>
              <Button
                variant={google.source === "google" ? "secondary" : "primary"}
                busy={busy === "src"}
                onClick={() => void run("src", () => post("/api/drive/source", { source: google.source === "google" ? "local" : "google" }), "Document source updated")}
              >
                {google.source === "google" ? "Use Demo Drive" : "Use Google Drive"}
              </Button>
              <Button variant="danger" busy={busy === "disc"} onClick={() => void run("disc", () => post("/api/drive/google/disconnect"), "Google Drive disconnected")}>
                Disconnect Google
              </Button>
            </>
          ) : google.google_configured ? (
            <a href="/api/drive/google/connect">
              <Button variant="primary" icon={<Cloud className="size-4" />}>
                Connect Google Drive
              </Button>
            </a>
          ) : (
            <div className="rounded-lg bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-muted">
              To connect Google Drive: create an OAuth client (type <i>Web application</i>) in Google Cloud Console with redirect URI{" "}
              <Code>http://localhost:8000/api/drive/google/callback</Code>, enable the Drive API, and save the JSON as{" "}
              <Code>credentials/google_oauth_client.json</Code>. See the README for details.
            </div>
          )}
        </div>
      </Row>

      <Row icon={<Mail className="size-4" />} title="Email" aside={<Badge tone={status.mail.transport === "smtp" ? "green" : "violet"}>{status.mail.transport === "smtp" ? "SMTP" : "Sandbox"}</Badge>}>
        {status.mail.transport === "smtp" ? (
          <>Emails are delivered through your SMTP server{status.mail.redirect ? <> and redirected to <b>{status.mail.redirect}</b> for safety</> : null}.</>
        ) : (
          <>
            Sandbox: nothing leaves this machine. Messages are saved as <Code>.eml</Code> files and shown in the Outbox. Set <Code>MAIL_TRANSPORT=smtp</Code>{" "}
            plus <Code>SMTP_*</Code> to deliver for real, and <Code>MAIL_REDIRECT_ALL_TO</Code> to route every email to one test inbox during demos.
          </>
        )}
      </Row>

      <Row icon={<ShieldCheck className="size-4" />} title="Guardrails">
        <ul className="list-disc space-y-1 pl-4">
          <li>Nothing is sent until counsel approves the draft; edits send it back for re-approval.</li>
          <li>The execution agent can only email addresses on file (the worker, HR, counsel) and cannot schedule reminders in the past.</li>
          <li>Wet-ink and written-form rules (e.g. § 623 BGB) are flagged on the draft and turned into hand-delivery reminders.</li>
        </ul>
      </Row>

      <Row icon={<RotateCcw className="size-4" />} title="Reset the demo">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>Clear every contract, draft, email, reminder and run, then start again from an empty desk.</span>
          <Button
            variant="danger"
            busy={busy === "reset"}
            className={cx(status.running.length > 0 && "opacity-50")}
            onClick={() => {
              if (window.confirm("Reset Handover? This deletes all analyses, drafts, emails and reminders in this workspace.")) void run("reset", () => post("/api/reset"), "Workspace reset");
            }}
          >
            Reset workspace
          </Button>
        </div>
      </Row>
      <p className="px-1 text-[12px] text-muted">
        Today for deadline math: <b>{status.today}</b> (pin it with <Code>HANDOVER_TODAY</Code> for repeatable demos). Counsel: {status.counsel.name} ·{" "}
        {status.counsel.email} · HR: {status.hr_email}
      </p>
    </div>
  );
}
