"""Stage 5: deliver approved documents, schedule follow-ups, and file final versions.

Guardrails live in the tools, not the prompt: only approved drafts can be sent,
only to addresses on file, and reminders cannot be scheduled in the past."""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from .. import db
from ..config import DATA_DIR, today
from ..connectors.drive import get_drive
from ..connectors.mail import send_mail
from ..events import changed
from .prompts import EXECUTION_SYSTEM, EXECUTION_USER
from .runtime import RunContext, ToolOutput, ToolSpec, run_agent


class ListIn(BaseModel):
    pass


class SendIn(BaseModel):
    draft_id: str
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str


class ReminderIn(BaseModel):
    contract_id: str
    due_date: str = Field(..., description="YYYY-MM-DD, today or later")
    recipients: list[str]
    subject: str
    message: str
    kind: Literal["follow_up", "signature", "delivery", "deadline"] = "follow_up"
    draft_id: str = ""


class FileIn(BaseModel):
    draft_id: str
    folder_path: str = Field(..., description="e.g. Legal/Employment/DE/Lukas Becker")


class CompleteIn(BaseModel):
    draft_id: str
    summary: str


def _allowed(contract: dict) -> set[str]:
    from ..config import get_settings

    st = get_settings()
    return {e.lower() for e in [contract.get("employee_email"), st.hr_email, st.counsel_email] if e}


def _pending() -> list[dict]:
    return db.query("SELECT * FROM drafts WHERE status IN ('approved','sent','filed') ORDER BY id")


async def list_approved_documents(ctx: RunContext, a: ListIn) -> ToolOutput:
    from ..config import get_settings

    st = get_settings()
    out = []
    for d in _pending():
        c = db.one("SELECT * FROM contracts WHERE id=?", (d["contract_id"],)) or {}
        out.append({
            "draft_id": d["id"], "contract_id": d["contract_id"], "worker": c.get("employee_name"),
            "worker_email_on_file": c.get("employee_email"), "country": c.get("country"), "title": d["title"],
            "doc_type": d["doc_type"], "language": d["language"], "english_summary": d["english_summary"],
            "compliance_notes": db.loads(d["compliance_notes_json"], []),
            "delivery_plan": db.loads(d["delivery_json"], {}), "status": d["status"],
            "already_emailed": bool(db.scalar("SELECT 1 FROM emails WHERE draft_id=? AND kind='document'", (d["id"],))),
            "already_filed": bool(db.scalar("SELECT 1 FROM filings WHERE draft_id=?", (d["id"],))),
            "contract_end_date": c.get("end_date"), "action_deadline": c.get("action_deadline"),
        })
    payload = {"today": today().isoformat(), "hr_email": st.hr_email, "counsel_email": st.counsel_email,
               "documents": out}
    return ToolOutput(content=json.dumps(payload, ensure_ascii=False), summary=f"{len(out)} approved document(s)")


async def send_document_email(ctx: RunContext, a: SendIn) -> ToolOutput:
    d = db.one("SELECT * FROM drafts WHERE id=?", (a.draft_id,))
    if not d or d["status"] not in ("approved", "sent", "filed"):
        return ToolOutput(content="Only documents counsel approved can be sent.", summary="Blocked: not approved",
                          is_error=True)
    c = db.one("SELECT * FROM contracts WHERE id=?", (d["contract_id"],)) or {}
    allowed = _allowed(c)
    bad = [x for x in a.to + a.cc if x.lower() not in allowed]
    if bad:
        return ToolOutput(content=f"Blocked: {', '.join(bad)} is not on file. Allowed: {', '.join(sorted(allowed))}.",
                          summary="Blocked: recipient not on file", is_error=True)
    attachments = []
    for rel, mime in ((d["pdf_path"], "application/pdf"),
                      (d["docx_path"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
        if rel and (DATA_DIR / rel).exists():
            attachments.append(((DATA_DIR / rel).name, (DATA_DIR / rel).read_bytes(), mime))
    result = await send_mail(a.to, a.cc, a.subject, a.body, attachments)
    db.insert("emails", {"kind": "document", "contract_id": d["contract_id"], "draft_id": d["id"],
                         "to_json": json.dumps(a.to), "cc_json": json.dumps(a.cc), "subject": a.subject,
                         "body": a.body, "attachments_json": json.dumps([x[0] for x in attachments]),
                         "status": result["status"], "transport": result["transport"], "created_at": db.now_iso(),
                         "sent_at": db.now_iso(), "run_id": ctx.run_id})
    if d["status"] == "approved":
        db.update("drafts", d["id"], {"status": "sent", "updated_at": db.now_iso()})
    db.log_activity(d["contract_id"], "sent", f"Emailed “{d['title']}” to {', '.join(a.to)}"
                    + (f" (cc {', '.join(a.cc)})" if a.cc else ""))
    ctx.emit("email.sent", draft_id=d["id"], to=a.to, cc=a.cc, subject=a.subject, transport=result["transport"])
    changed("outbox", "drafts", "contracts", "overview")
    return ToolOutput(content=f"Sent ({result['detail']}).", summary=f"Emailed {', '.join(a.to)} · {result['transport']}")


async def schedule_reminder(ctx: RunContext, a: ReminderIn) -> ToolOutput:
    try:
        due = date.fromisoformat(a.due_date[:10])
    except ValueError:
        return ToolOutput(content="due_date must be YYYY-MM-DD.", summary="Bad date", is_error=True)
    if due < today():
        return ToolOutput(content=f"{a.due_date} is in the past; today is {today().isoformat()}.",
                          summary="Date in the past", is_error=True)
    c = db.one("SELECT * FROM contracts WHERE id=?", (a.contract_id,))
    if not c:
        return ToolOutput(content="Unknown contract id.", summary="Unknown contract", is_error=True)
    allowed = _allowed(c)
    bad = [x for x in a.recipients if x.lower() not in allowed]
    if bad:
        return ToolOutput(content=f"Blocked: {', '.join(bad)} is not on file. Allowed: {', '.join(sorted(allowed))}.",
                          summary="Blocked: recipient not on file", is_error=True)
    rid = db.insert("reminders", {"contract_id": a.contract_id, "draft_id": a.draft_id or None,
                                  "due_date": due.isoformat(), "recipients_json": json.dumps(a.recipients),
                                  "subject": a.subject, "message": a.message, "kind": a.kind, "status": "scheduled",
                                  "created_by": "agent", "created_at": db.now_iso(), "run_id": ctx.run_id})
    db.log_activity(a.contract_id, "reminder", f"Reminder for {due.isoformat()}: {a.subject}")
    ctx.emit("reminder.scheduled", reminder_id=rid, contract_id=a.contract_id, due_date=due.isoformat(),
             subject=a.subject, kind=a.kind)
    changed("reminders", "contracts")
    return ToolOutput(content=f"Reminder #{rid} scheduled for {due.isoformat()}.",
                      summary=f"{due.isoformat()} · {a.kind} · {', '.join(a.recipients)}")


async def file_to_drive(ctx: RunContext, a: FileIn) -> ToolOutput:
    d = db.one("SELECT * FROM drafts WHERE id=?", (a.draft_id,))
    if not d or d["status"] not in ("approved", "sent", "filed"):
        return ToolOutput(content="Only approved documents can be filed.", summary="Blocked: not approved",
                          is_error=True)
    rel = d["pdf_path"] or d["docx_path"]
    if not rel or not (DATA_DIR / rel).exists():
        return ToolOutput(content="The rendered file for this draft is missing; ask counsel to re-save the draft.",
                          summary="File missing", is_error=True)
    path = DATA_DIR / rel
    mime = "application/pdf" if rel.endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    drive = ctx.state.get("drive") or get_drive()
    res = await drive.upload(a.folder_path, path.name, path.read_bytes(), mime)
    db.insert("filings", {"draft_id": d["id"], "contract_id": d["contract_id"], "folder_path": a.folder_path,
                          "file_name": path.name, "drive_ref": res["ref"], "web_link": res.get("web_link"),
                          "created_at": db.now_iso(), "run_id": ctx.run_id})
    db.update("drafts", d["id"], {"status": "filed", "updated_at": db.now_iso()})
    db.log_activity(d["contract_id"], "filed", f"Filed final version to {res['location']}")
    ctx.emit("document.filed", draft_id=d["id"], location=res["location"], web_link=res.get("web_link"))
    changed("filings", "drafts", "contracts")
    return ToolOutput(content=f"Filed to {res['location']}.", summary=res["location"])


async def complete_execution(ctx: RunContext, a: CompleteIn) -> ToolOutput:
    d = db.one("SELECT * FROM drafts WHERE id=?", (a.draft_id,))
    if not d:
        return ToolOutput(content="Unknown draft id.", summary="Unknown draft", is_error=True)
    db.update("drafts", d["id"], {"status": "executed", "updated_at": db.now_iso()})
    if d["decision_id"]:
        db.update("decisions", d["decision_id"], {"status": "executed", "updated_at": db.now_iso()})
    # the decision is made and delivered: decide-by alerts give way to the execution follow-ups
    db.execute("UPDATE reminders SET status='cancelled' WHERE contract_id=? AND kind='deadline' "
               "AND created_by='deadline-guard' AND status='scheduled'", (d["contract_id"],))
    db.log_activity(d["contract_id"], "executed", a.summary)
    changed("drafts", "contracts", "overview", "reminders")
    return ToolOutput(content="Marked complete.", summary=a.summary[:160])


def execution_tools() -> list[ToolSpec]:
    return [
        ToolSpec("list_approved_documents", "List every document counsel approved that still needs executing, with "
                 "its delivery plan, compliance notes and the addresses on file. Call this first.",
                 ListIn, list_approved_documents, lambda i: "Collecting approved documents"),
        ToolSpec("send_document_email", "Email an approved document (PDF + DOCX attached) to addresses on file.",
                 SendIn, send_document_email, lambda i: f"Emailing {', '.join(i.get('to') or [])}"),
        ToolSpec("schedule_reminder", "Schedule a reminder email (signature follow-up, wet-ink delivery, upcoming "
                 "deadline). Recipients must be on file.", ReminderIn, schedule_reminder,
                 lambda i: f"Scheduling {i.get('kind', 'follow_up').replace('_', ' ')} for {i.get('due_date', '')}"),
        ToolSpec("file_to_drive", "Save the final version of an approved document into the document store.",
                 FileIn, file_to_drive, lambda i: f"Filing to {i.get('folder_path', '')}"),
        ToolSpec("complete_execution", "Mark one document as fully executed once it is sent, reminded and filed.",
                 CompleteIn, complete_execution, lambda i: f"Closing {i.get('draft_id', '')}"),
    ]


async def run(ctx: RunContext) -> str:
    from . import simulator

    drafts = db.query("SELECT * FROM drafts WHERE status='approved' ORDER BY id")
    if not drafts:
        return "Nothing approved to execute. Approve drafts first."
    ctx.state["drive"] = get_drive()
    tools = execution_tools()
    s = ctx.settings
    if ctx.mode == "live":
        await run_agent(ctx, system=EXECUTION_SYSTEM.format(company=s.company_name, counsel=s.counsel_name),
                        user=EXECUTION_USER.format(today=today().isoformat()), tools=tools, max_turns=40)
    else:
        await simulator.execution(ctx, tools, drafts)
    ids = [d["id"] for d in drafts]
    marks = ",".join("?" * len(ids))
    sent = db.scalar(f"SELECT COUNT(*) FROM emails WHERE kind='document' AND draft_id IN ({marks})", ids)
    rem = db.scalar(f"SELECT COUNT(*) FROM reminders WHERE created_by='agent' AND draft_id IN ({marks})", ids) or 0
    filed = db.scalar(f"SELECT COUNT(*) FROM filings WHERE draft_id IN ({marks})", ids)
    return f"Executed {len(ids)} document(s): {sent} email(s) sent, {rem} reminder(s) scheduled, {filed} filed."
