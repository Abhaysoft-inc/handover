"""Handover API: REST endpoints for the dashboard plus a server-sent event stream
that carries every agent step live."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import uuid
from datetime import timedelta
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, deadlines, scheduler
from .agents import discovery, drafting, execution, knowledge, runtime
from .config import DATA_DIR, FRONTEND_DIST, get_settings, today
from .connectors import drive as drive_mod
from .events import bus, changed, sse_format

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    bus.bind(asyncio.get_running_loop())
    db.conn()
    db.execute("UPDATE runs SET status='failed', error='Server restarted during the run', finished_at=? "
               "WHERE status='running'", (db.now_iso(),))
    task = asyncio.create_task(scheduler.loop())
    yield
    task.cancel()


app = FastAPI(title="Handover", lifespan=lifespan)


# ── helpers ───────────────────────────────────────────────────────────────


def _risk_counts(c: dict) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0}
    for r in db.loads(c.get("risk_flags_json"), []):
        if r.get("severity") in counts:
            counts[r["severity"]] += 1
    return counts


def _contract_summary(c: dict) -> dict:
    label, days = deadlines.urgency(c["action_deadline"])
    decision = db.one("SELECT id, action, params_json, status FROM decisions WHERE contract_id=?", (c["id"],))
    drafts = db.query("SELECT id, title, status FROM drafts WHERE contract_id=? AND status!='superseded' ORDER BY id",
                      (c["id"],))
    primary = db.one("SELECT id, name, path, is_scanned, language_name FROM documents WHERE id=?",
                     (c["primary_document_id"],))
    return {
        **{k: c[k] for k in ("id", "employee_name", "employee_email", "job_title", "department", "employer_entity",
                             "country", "work_location", "governing_law", "language", "language_name",
                             "contract_type", "start_date", "end_date", "action_deadline", "action_deadline_reason",
                             "compensation_text", "non_compete", "recommended_action", "recommendation_rationale",
                             "status_note", "summary", "notice_terms", "renewal_terms", "probation_end_date")},
        "auto_renewal": bool(c["auto_renewal"]), "signed": c["signed"],
        "urgency": label, "days_left": days, "risks": _risk_counts(c),
        "decision": ({**decision, "params": db.loads(decision.pop("params_json"), {})} if decision else None),
        "drafts": drafts, "primary_document": primary,
    }


def _overview() -> dict:
    docs = db.query("SELECT status, is_employment, is_scanned, language FROM documents")
    contracts = db.query("SELECT * FROM contracts")
    urg: dict[str, int] = {}
    for c in contracts:
        u = deadlines.urgency(c["action_deadline"])[0]
        urg[u] = urg.get(u, 0) + 1
    risks = {"high": 0, "medium": 0, "low": 0}
    for c in contracts:
        for k, v in _risk_counts(c).items():
            risks[k] += v
    dec = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM decisions GROUP BY status")}
    drf = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM drafts GROUP BY status")}
    analyzed = sum(1 for d in docs if d["status"] == "analyzed")
    skipped = sum(1 for d in docs if d["status"] == "skipped")
    langs = {d["language"] for d in docs if d["language"] and d["status"] == "analyzed"}
    scanned = sum(1 for d in docs if d["is_scanned"] and d["status"] == "analyzed")
    emails = db.scalar("SELECT COUNT(*) FROM emails WHERE kind='document'") or 0
    rem_sched = db.scalar("SELECT COUNT(*) FROM reminders WHERE status='scheduled'") or 0
    rem_sent = db.scalar("SELECT COUNT(*) FROM reminders WHERE status='sent'") or 0
    filings = db.scalar("SELECT COUNT(*) FROM filings") or 0
    kb = db.scalar("SELECT COUNT(*) FROM kb") or 0
    n_dec = sum(dec.values())
    running = {r["kind"] for r in runtime.active_runs()}
    approved_waiting = drf.get("approved", 0)
    executed = drf.get("executed", 0) + drf.get("filed", 0) + drf.get("sent", 0)

    def st(done: bool, active: bool, kinds: set[str]) -> str:
        if running & kinds:
            return "running"
        return "done" if done else ("active" if active else "todo")

    stages = [
        {"key": "ingest", "title": "Ingest", "status": st(bool(docs), not docs, {"discovery"}),
         "detail": f"{len(docs)} files found" if docs else "Connect a Drive"},
        {"key": "analyze", "title": "Analyze", "status": st(analyzed > 0, False, {"discovery"}),
         "detail": f"{analyzed} read · {scanned} scanned · {len(langs)} languages" if analyzed else "Bulk analysis"},
        {"key": "review", "title": "Review", "status": st(n_dec > 0, bool(contracts) and n_dec == 0, set()),
         "detail": f"{n_dec} of {len(contracts)} decided" if contracts else "Assign actions"},
        {"key": "draft", "title": "Draft", "status": st(sum(drf.values()) > 0 and not dec.get("pending_draft"),
                                                          bool(dec.get("pending_draft")), {"drafting"}),
         "detail": f"{sum(drf.values())} drafted · {drf.get('approved', 0)} approved" if drf else "Addenda & letters"},
        {"key": "execute", "title": "Execute", "status": st(executed > 0 and not approved_waiting,
                                                              approved_waiting > 0, {"execution"}),
         "detail": f"{emails} sent · {rem_sched} reminders · {filings} filed" if emails or filings else "Send, remind, file"},
        {"key": "knowledge", "title": "Knowledge", "status": "done" if kb else "todo",
         "detail": f"{kb} records searchable" if kb else "Ask anything"},
    ]
    briefing = db.one("SELECT markdown, created_at, run_id FROM briefings ORDER BY id DESC LIMIT 1")
    return {
        "documents": {"total": len(docs), "analyzed": analyzed, "skipped": skipped, "scanned": scanned,
                      "employment": sum(1 for d in docs if d["is_employment"]), "languages": sorted(langs)},
        "contracts": {"total": len(contracts), "countries": sorted({c["country"] for c in contracts if c["country"]}),
                      "urgency": urg},
        "risks": risks, "issues": db.scalar("SELECT COUNT(*) FROM issues") or 0,
        "decisions": dec, "drafts": drf, "emails": emails,
        "reminders": {"scheduled": rem_sched, "sent": rem_sent}, "filings": filings, "kb_records": kb,
        "stages": stages, "briefing": briefing,
    }


# ── status & overview ─────────────────────────────────────────────────────


@app.get("/api/status")
def status() -> dict:
    s = get_settings()
    model = s.model
    if s.provider == "gemini" and model == "auto":
        from .agents import gemini

        model = gemini._resolved.get("auto", "auto (best available Gemini Flash)")
    return {
        "mode": s.mode, "mode_setting": s.mode_setting, "has_credentials": s.has_credentials, "model": model,
        "provider": s.provider, "provider_label": s.provider_label,
        "effort": s.effort, "company": s.company_name, "counsel": {"name": s.counsel_name, "email": s.counsel_email},
        "hr_email": s.hr_email, "today": today().isoformat(), "drive": drive_mod.drive_status(),
        "mail": {"transport": "smtp" if (s.mail_transport == "smtp" and s.smtp_host) else "sandbox",
                 "redirect": s.mail_redirect_all_to or None},
        "running": runtime.active_runs(),
    }


@app.get("/api/overview")
def overview() -> dict:
    return _overview()


# ── documents ─────────────────────────────────────────────────────────────


@app.get("/api/documents")
def documents() -> list[dict]:
    return db.query("SELECT id, name, path, mime_type, status, skip_reason, kind, is_employment, language, "
                    "language_name, is_scanned, title, summary, pages, error FROM documents ORDER BY path, name")


@app.get("/api/documents/{doc_id}/file")
async def document_file(doc_id: str) -> Response:
    d = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not d:
        raise HTTPException(404, "Unknown document")
    drive = drive_mod.get_drive()
    if d["source"] != drive.source:
        raise HTTPException(409, "That document belongs to a different Drive source")
    data, mime = await drive.download(d["source_ref"])
    disposition = "inline" if mime in ("application/pdf",) or mime.startswith("image/") else "attachment"
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{_quote(d['name'])}"})


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name)


# ── contracts & decisions ─────────────────────────────────────────────────


@app.get("/api/contracts")
def contracts() -> list[dict]:
    rows = [_contract_summary(c) for c in db.query("SELECT * FROM contracts")]
    rows.sort(key=lambda r: (deadlines.URGENCY_ORDER[r["urgency"]], r["action_deadline"] or "9999"))
    return rows


@app.get("/api/contracts/{cid}")
def contract(cid: str) -> dict:
    c = db.one("SELECT * FROM contracts WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Unknown contract")
    doc_ids = [c["primary_document_id"], *db.loads(c["supporting_document_ids"], [])]
    return {
        **_contract_summary(c),
        "notice_period_days": c["notice_period_days"], "termination_terms": c["termination_terms"],
        "confidence": c["confidence"],
        "key_dates": db.loads(c["key_dates_json"], []), "key_clauses": db.loads(c["key_clauses_json"], []),
        "risk_flags": db.loads(c["risk_flags_json"], []),
        "documents": [db.one("SELECT id, name, path, kind, language_name, is_scanned, summary, pages FROM documents "
                             "WHERE id=?", (i,)) for i in doc_ids],
        "issues": db.query("SELECT * FROM issues WHERE contract_id=?", (cid,)),
        "activity": db.query("SELECT ts, kind, text FROM activity WHERE contract_id=? ORDER BY id DESC", (cid,)),
        "reminders": db.query("SELECT * FROM reminders WHERE contract_id=? ORDER BY due_date", (cid,)),
    }


class DecisionIn(BaseModel):
    action: Literal["renew", "non_renew", "terminate", "amend", "convert_to_permanent", "confirm_probation",
                    "no_action"]
    params: dict[str, Any] = {}


@app.post("/api/contracts/{cid}/decision")
def set_decision(cid: str, body: DecisionIn) -> dict:
    c = db.one("SELECT * FROM contracts WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Unknown contract")
    params = {k: v for k, v in body.params.items() if v not in (None, "")}
    existing = db.one("SELECT * FROM decisions WHERE contract_id=?", (cid,))
    if existing and existing["status"] == "executed":
        raise HTTPException(409, "This decision was already executed")
    db.execute("UPDATE drafts SET status='superseded' WHERE contract_id=? AND status IN ('draft','approved')", (cid,))
    status_ = "no_action" if body.action == "no_action" else "pending_draft"
    if existing:
        db.update("decisions", existing["id"], {"action": body.action, "params_json": json.dumps(params),
                                                "status": status_, "updated_at": db.now_iso()})
    else:
        db.insert("decisions", {"contract_id": cid, "action": body.action, "params_json": json.dumps(params),
                                "status": status_, "created_at": db.now_iso(), "updated_at": db.now_iso()})
    label = drafting.ACTION_LABELS.get(body.action, "No action")
    db.log_activity(cid, "decision", f"Counsel decided: {label}" + (f" — {json.dumps(params)}" if params else ""))
    changed("contracts", "overview", "drafts")
    return _contract_summary(db.one("SELECT * FROM contracts WHERE id=?", (cid,)))


@app.delete("/api/contracts/{cid}/decision")
def clear_decision(cid: str) -> dict:
    d = db.one("SELECT * FROM decisions WHERE contract_id=?", (cid,))
    if d and d["status"] == "executed":
        raise HTTPException(409, "This decision was already executed")
    db.execute("DELETE FROM decisions WHERE contract_id=?", (cid,))
    db.execute("UPDATE drafts SET status='superseded' WHERE contract_id=? AND status IN ('draft','approved')", (cid,))
    db.log_activity(cid, "decision", "Decision cleared")
    changed("contracts", "overview", "drafts")
    return {"ok": True}


@app.get("/api/issues")
def issues() -> list[dict]:
    return db.query("SELECT i.*, c.employee_name FROM issues i LEFT JOIN contracts c ON c.id=i.contract_id "
                    "ORDER BY CASE i.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, i.id")


# ── drafts ────────────────────────────────────────────────────────────────


def _draft_out(d: dict) -> dict:
    c = db.one("SELECT employee_name, country, language_name FROM contracts WHERE id=?", (d["contract_id"],)) or {}
    return {**{k: v for k, v in d.items() if not k.endswith("_json")},
            "compliance_notes": db.loads(d["compliance_notes_json"], []), "delivery": db.loads(d["delivery_json"], {}),
            "employee_name": c.get("employee_name"), "country": c.get("country"),
            "contract_language": c.get("language_name")}


@app.get("/api/drafts")
def drafts() -> list[dict]:
    return [_draft_out(d) for d in db.query("SELECT * FROM drafts WHERE status!='superseded' ORDER BY id DESC")]


@app.get("/api/drafts/{did}")
def draft(did: str) -> dict:
    d = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    if not d:
        raise HTTPException(404, "Unknown draft")
    return _draft_out(d)


class DraftEdit(BaseModel):
    title: str | None = None
    body_markdown: str | None = None


@app.put("/api/drafts/{did}")
def edit_draft(did: str, body: DraftEdit) -> dict:
    d = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    if not d:
        raise HTTPException(404, "Unknown draft")
    if d["status"] not in ("draft", "approved"):
        raise HTTPException(409, "Sent documents can't be edited")
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update("drafts", did, {**changes, "status": "draft", "version": (d["version"] or 1) + 1,
                              "updated_at": db.now_iso()})
    drafting.render_files(did)
    db.log_activity(d["contract_id"], "edited", f"Counsel edited {did} (v{(d['version'] or 1) + 1})")
    changed("drafts")
    return draft(did)


@app.post("/api/drafts/{did}/approve")
def approve(did: str) -> dict:
    d = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    if not d or d["status"] != "draft":
        raise HTTPException(409, "Only drafts awaiting review can be approved")
    db.update("drafts", did, {"status": "approved", "approved_at": db.now_iso(), "updated_at": db.now_iso()})
    if d["decision_id"]:
        db.update("decisions", d["decision_id"], {"status": "approved", "updated_at": db.now_iso()})
    db.log_activity(d["contract_id"], "approved", f"Counsel approved “{d['title']}”")
    changed("drafts", "overview", "contracts")
    return draft(did)


@app.post("/api/drafts/{did}/unapprove")
def unapprove(did: str) -> dict:
    d = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    if not d or d["status"] != "approved":
        raise HTTPException(409, "Only approved drafts can be sent back")
    db.update("drafts", did, {"status": "draft", "approved_at": None, "updated_at": db.now_iso()})
    if d["decision_id"]:
        db.update("decisions", d["decision_id"], {"status": "drafted"})
    changed("drafts", "overview", "contracts")
    return draft(did)


@app.get("/api/drafts/{did}/download")
def download_draft(did: str, format: Literal["pdf", "docx"] = "pdf"):
    d = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    rel = d and (d["pdf_path"] if format == "pdf" else d["docx_path"])
    if not rel or not (DATA_DIR / rel).exists():
        raise HTTPException(404, "File not available")
    path = DATA_DIR / rel
    return FileResponse(path, filename=path.name, content_disposition_type="inline" if format == "pdf" else "attachment")


# ── execution artefacts ───────────────────────────────────────────────────


@app.get("/api/outbox")
def outbox() -> list[dict]:
    rows = db.query("SELECT e.*, c.employee_name FROM emails e LEFT JOIN contracts c ON c.id=e.contract_id "
                    "ORDER BY e.id DESC")
    return [{**r, "to": db.loads(r.pop("to_json"), []), "cc": db.loads(r.pop("cc_json"), []),
             "attachments": db.loads(r.pop("attachments_json"), [])} for r in rows]


@app.get("/api/reminders")
def reminders() -> list[dict]:
    rows = db.query("SELECT r.*, c.employee_name FROM reminders r LEFT JOIN contracts c ON c.id=r.contract_id "
                    "ORDER BY CASE r.status WHEN 'scheduled' THEN 0 ELSE 1 END, r.due_date, r.id")
    return [{**r, "recipients": db.loads(r.pop("recipients_json"), [])} for r in rows]


class SweepIn(BaseModel):
    days_ahead: int = 0


@app.post("/api/reminders/sweep")
async def sweep(body: SweepIn) -> dict:
    as_of = today() + timedelta(days=max(0, body.days_ahead))
    fired = await scheduler.sweep(as_of)
    return {"as_of": as_of.isoformat(), "fired": fired}


@app.get("/api/filings")
def filings() -> list[dict]:
    return db.query("SELECT f.*, c.employee_name FROM filings f LEFT JOIN contracts c ON c.id=f.contract_id "
                    "ORDER BY f.id DESC")


@app.get("/api/filings/{fid}/file")
def filing_file(fid: int):
    f = db.one("SELECT * FROM filings WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404, "Unknown filing")
    if f["web_link"]:
        return RedirectResponse(f["web_link"])
    path = (DATA_DIR / "drive_filings" / f["drive_ref"]).resolve()
    if not path.is_relative_to((DATA_DIR / "drive_filings").resolve()) or not path.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


# ── runs & live events ────────────────────────────────────────────────────

RUNNERS = {
    "discovery": ("Discovery: ingest, analyse and brief", discovery.run),
    "drafting": ("Drafting: prepare documents for review", drafting.run),
    "execution": ("Execution: send, remind and file", execution.run),
}


@app.post("/api/runs/{kind}")
async def start(kind: str) -> dict:
    if kind not in RUNNERS:
        raise HTTPException(404, "Unknown run type")
    busy = runtime.busy_with(runtime.EXCLUSIVE)
    if busy:
        raise HTTPException(409, f"Another workflow run is in progress ({busy}).")
    title, work = RUNNERS[kind]
    return {"run_id": runtime.start_run(kind, title, work)}


@app.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str) -> dict:
    return {"cancelled": runtime.cancel_run(run_id)}


@app.get("/api/runs")
def runs(limit: int = 30) -> list[dict]:
    rows = db.query("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
    return [{**r, "usage": db.loads(r.pop("usage_json"), None)} for r in rows]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    r = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "Unknown run")
    events = db.query("SELECT seq, ts, type, data_json FROM run_events WHERE run_id=? ORDER BY seq", (run_id,))
    return {**r, "usage": db.loads(r.pop("usage_json"), None),
            "events": [{"run_id": run_id, "kind": r["kind"], "seq": e["seq"], "ts": e["ts"], "type": e["type"],
                        "data": db.loads(e["data_json"], {})} for e in events]}


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            yield sse_format({"type": "hello", "data": {"running": runtime.active_runs()}})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield sse_format(ev)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── knowledge base ────────────────────────────────────────────────────────


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None


@app.post("/api/ask")
async def ask(body: AskIn) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask a question")
    conv = body.conversation_id or "K-" + uuid.uuid4().hex[:8]
    run_id = runtime.start_run("ask", f"Q: {question[:80]}", lambda ctx: knowledge.ask(ctx, question, conv))
    return {"run_id": run_id, "conversation_id": conv}


@app.get("/api/conversations/{conv}")
def conversation(conv: str) -> list[dict]:
    return db.query("SELECT role, content, run_id, created_at FROM chat_messages WHERE conversation_id=? ORDER BY id",
                    (conv,))


# ── settings: drive source, Google OAuth, reset ───────────────────────────


class SourceIn(BaseModel):
    source: Literal["local", "google"]


@app.post("/api/drive/source")
def set_source(body: SourceIn) -> dict:
    if body.source == "google" and not drive_mod.google_connected():
        raise HTTPException(409, "Connect Google Drive first")
    db.kv_set("drive_source", body.source)
    changed("status")
    return drive_mod.drive_status()


@app.get("/api/drive/google/connect")
def google_connect(request: Request):
    if not drive_mod.google_configured():
        raise HTTPException(400, "Add an OAuth client file first (see README → Google Drive setup).")
    redirect_uri = str(request.url_for("google_callback"))
    return RedirectResponse(drive_mod.google_auth_url(redirect_uri))


@app.get("/api/drive/google/callback", name="google_callback")
def google_callback(request: Request, state: str = ""):
    try:
        drive_mod.google_finish_auth(state, str(request.url))
    except Exception as e:
        return RedirectResponse(f"/#/settings?drive_error={_quote(str(e)[:200])}")
    db.kv_set("drive_source", "google")
    changed("status")
    return RedirectResponse("/#/settings?drive=connected")


@app.post("/api/drive/google/disconnect")
def google_disconnect() -> dict:
    db.execute("DELETE FROM kv WHERE k='google_token'")
    db.kv_set("drive_source", "local")
    changed("status")
    return drive_mod.drive_status()


@app.post("/api/reset")
def reset() -> dict:
    if runtime.active_runs():
        raise HTTPException(409, "Stop running workflows before resetting")
    db.reset_all()
    for sub in ("drafts", "outbox", "drive_filings"):
        shutil.rmtree(DATA_DIR / sub, ignore_errors=True)
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
    changed("contracts", "overview", "documents", "drafts", "outbox", "reminders", "filings", "runs", "briefing",
            "issues")
    return {"ok": True}


# ── the dashboard ─────────────────────────────────────────────────────────

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
else:
    @app.get("/")
    def no_ui() -> Response:
        return Response("<h1>Handover API is running</h1><p>Build the dashboard with <code>npm run build</code> in "
                        "<code>frontend/</code>, or run the Vite dev server on port 5173.</p>",
                        media_type="text/html")
