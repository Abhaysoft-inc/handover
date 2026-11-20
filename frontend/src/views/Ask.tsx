import { ArrowUp, BookOpen, LoaderCircle, MessageSquareText, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { post } from "../api";
import { useAppData, useToast } from "../context";
import { navigate, useRun } from "../live";
import { Button, Card, Markdown, cx } from "../ui";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = [
  "Which deadlines can't I miss in the next 30 days?",
  "Who has a post-employment non-compete, and is it enforceable?",
  "What would it cost us not to renew Daniel O'Connor?",
  "Which contracts carry legal risk under local law?",
  "Summarise everything we have in Germany.",
  "Who can sign contract changes, and in what form?",
];

export function Ask() {
  const { overview, status } = useAppData();
  const toast = useToast();
  const [conv, setConv] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const run = useRun(runId);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (run && run.status !== "running" && runId) {
      const text = run.answer ?? (run.status === "failed" ? `Sorry, that failed: ${run.error ?? "unknown error"}` : run.text);
      setMessages((m) => [...m, { role: "assistant", content: text || "No answer." }]);
      setRunId(null);
    }
  }, [run, runId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, run?.text, run?.items.length]);

  async function ask(q: string) {
    const question = q.trim();
    if (!question || runId) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }]);
    try {
      const r = await post<{ run_id: string; conversation_id: string }>("/api/ask", { question, conversation_id: conv });
      setConv(r.conversation_id);
      setRunId(r.run_id);
    } catch (e) {
      toast({ tone: "error", title: "Could not ask", body: e instanceof Error ? e.message : String(e) });
    }
  }

  const empty = !(overview?.kb_records ?? 0);
  const steps = run?.items.filter((i) => i.kind === "tool") ?? [];

  return (
    <div className="mx-auto flex h-full max-w-[920px] flex-col px-8 py-7">
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-[0.12em] text-muted">Step 6 · Knowledge</div>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight text-ink-950">Ask the archive</h1>
          <p className="mt-1 text-[14px] text-muted">
            {overview?.kb_records ?? 0} records searchable: contracts, clauses in every language, drafts and the memo. Answers cite their sources.
          </p>
        </div>
        {messages.length > 0 && (
          <Button variant="ghost" icon={<RotateCcw className="size-4" />} onClick={() => { setMessages([]); setConv(null); }}>
            New thread
          </Button>
        )}
      </div>

      <div className="flex-1 space-y-5 pb-4">
        {messages.length === 0 && (
          <Card className="px-6 py-6">
            {empty ? (
              <div className="text-center text-sm text-muted">
                The knowledge base fills up during discovery.{" "}
                <button type="button" className="text-brand underline" onClick={() => navigate("")}>
                  Run discovery first
                </button>
                .
              </div>
            ) : (
              <>
                <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-ink-950">
                  <Sparkles className="size-4 text-amber-500" /> Try asking
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" onClick={() => void ask(s)} className="rounded-lg border border-line px-3.5 py-2.5 text-left text-[13px] text-ink-800 hover:border-brand hover:bg-brand-50/40">
                      {s}
                    </button>
                  ))}
                </div>
              </>
            )}
          </Card>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-md bg-ink-950 px-4 py-2.5 text-[14px] text-white">{m.content}</div>
            </div>
          ) : (
            <div key={i} className="flex gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-ink-950 text-brass">
                <BookOpen className="size-4" />
              </div>
              <Card className="min-w-0 flex-1 px-5 py-4">
                <Markdown text={m.content} />
              </Card>
            </div>
          ),
        )}

        {runId && (
          <div className="flex gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-ink-950 text-brass">
              <LoaderCircle className="size-4 animate-spin" />
            </div>
            <Card className="min-w-0 flex-1 px-5 py-4">
              {steps.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {steps.map((s) => (
                    <span key={s.id} className={cx("inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11.5px]", s.state === "running" ? "bg-brand-50 text-brand" : "bg-slate-100 text-muted")}>
                      {s.state === "running" && <LoaderCircle className="size-3 animate-spin" />}
                      {s.label}
                    </span>
                  ))}
                </div>
              )}
              {run?.text ? <Markdown text={run.text} /> : <div className="text-[13px] text-muted">Searching the archive…</div>}
            </Card>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
        className="sticky bottom-4"
      >
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-2 shadow-lg focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/20">
          <MessageSquareText className="mb-2.5 ml-2 size-4 shrink-0 text-slate-400" />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask(input);
              }
            }}
            rows={1}
            placeholder={empty ? "Run discovery to build the archive first" : `Ask about ${status?.company ?? "the company"}'s contracts…`}
            className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-1 py-2 text-[14px] outline-none"
          />
          <button type="submit" disabled={!input.trim() || !!runId} className="flex size-9 items-center justify-center rounded-xl bg-brand text-white transition hover:bg-brand-600 disabled:opacity-40">
            <ArrowUp className="size-4" />
          </button>
        </div>
      </form>
    </div>
  );
}
