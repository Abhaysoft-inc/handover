"""Stages 1-3: ingest the Drive, analyse documents in parallel, reconcile them into
one contract per worker, track deadlines, and brief counsel."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, Field

from .. import db, deadlines
from ..config import today
from ..connectors.drive import DriveFile, doc_id, get_drive
from ..events import changed
from . import analyst
from .prompts import DISCOVERY_SYSTEM, DISCOVERY_USER
from .runtime import RunContext, ToolOutput, ToolSpec, run_agent
from .schemas import ACTIONS

SHORT_MIME = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "text/plain": "txt",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
              "application/vnd.google-apps.document": "google-doc"}


def ensure_document(ctx: RunContext, drive, f: DriveFile) -> str:
    did = doc_id(drive.source, f.ref)
    existing = db.one("SELECT id, status FROM documents WHERE id=?", (did,))
    if not existing:
        db.insert("documents", {"id": did, "source": drive.source, "source_ref": f.ref, "name": f.name,
                                "path": f.path, "mime_type": f.mime_type, "size": f.size,
                                "modified_at": f.modified_at, "status": "discovered", "run_id": ctx.run_id})
        ctx.emit("doc.status", document_id=did, name=f.name, path=f.path, status="discovered",
                 detail=SHORT_MIME.get(f.mime_type, f.mime_type.split("/")[-1]))
    return did


# ── tool inputs ───────────────────────────────────────────────────────────


class ListFolderIn(BaseModel):
    folder_id: str = Field("root", description='Folder id from a previous listing, or "root" for the top level.')
    recursive: bool = Field(False, description="List every file below this folder in one call (all sub-folders "
                                               "included). Use it for the first overview of the Drive.")


class SearchIn(BaseModel):
    query: str = Field(..., description="Words to look for in file names and contents.")


class AnalyzeIn(BaseModel):
    document_ids: list[str] = Field(..., description="Ids of the documents to read. Batch many in one call.")


class SkipItem(BaseModel):
    document_id: str
    reason: str = Field(..., description="Why it is out of scope, in a few words.")


class SkipIn(BaseModel):
    items: list[SkipItem]


class GetAnalysisIn(BaseModel):
    document_id: str


class RegisterIn(BaseModel):
    employee_name: str
    primary_document_id: str = Field(..., description="The main agreement or offer letter for this worker.")
    supporting_document_ids: list[str] = Field(default_factory=list,
                                               description="Amendments and other documents about this worker.")
    effective_end_date: str = Field("", description="End of the current term after amendments (YYYY-MM-DD); "
                                                    "empty if open-ended.")
    action_deadline: str = Field("", description="Next date counsel must act by (YYYY-MM-DD); empty if none.")
    action_deadline_reason: str = Field("", description="What must happen by that date and what happens if missed.")
    current_compensation: str = Field("", description="Current pay after amendments, short text.")
    recommended_action: Literal[tuple(ACTIONS)]  # type: ignore[valid-type]
    recommendation_rationale: str
    status_note: str = Field("", description="Anything counsel should know at a glance (missed windows etc.).")


class IssueIn(BaseModel):
    severity: Literal["high", "medium", "low"]
    title: str
    detail: str
    employee_name: str = Field("", description="Worker the issue concerns, if any.")


class BriefingIn(BaseModel):
    markdown: str = Field(..., description="The briefing memo for counsel, in Markdown.")


# ── handlers ──────────────────────────────────────────────────────────────


async def list_drive_folder(ctx: RunContext, a: ListFolderIn) -> ToolOutput:
    drive = ctx.state["drive"]
    queue, out, files, folders = [a.folder_id], [], 0, 0
    while queue and files < 500:
        for f in await drive.list_folder(queue.pop(0)):
            if f.is_folder:
                folders += 1
                if a.recursive:
                    queue.append(f.ref)
                else:
                    out.append({"type": "folder", "folder_id": f.ref, "name": f.name})
            else:
                files += 1
                item = {"type": "file", "document_id": ensure_document(ctx, drive, f), "name": f.name,
                        "format": SHORT_MIME.get(f.mime_type, f.mime_type), "size_kb": round((f.size or 0) / 1024),
                        "modified": f.modified_at}
                if a.recursive:
                    item["folder"] = f.path
                out.append(item)
        if not a.recursive:
            break
    where = "/" if a.folder_id in ("", "root", "/") else a.folder_id
    changed("documents")
    return ToolOutput(content=json.dumps({"folder": where, "recursive": a.recursive, "items": out}, ensure_ascii=False),
                      summary=f"{files} file{'s' * (files != 1)}, {folders} folder{'s' * (folders != 1)}"
                              + (" (whole tree)" if a.recursive else ""))


async def search_drive(ctx: RunContext, a: SearchIn) -> ToolOutput:
    drive = ctx.state["drive"]
    hits = await drive.search(a.query)
    out = [{"document_id": ensure_document(ctx, drive, f), "name": f.name, "path": f.path} for f in hits[:40]]
    changed("documents")
    return ToolOutput(content=json.dumps({"query": a.query, "results": out}, ensure_ascii=False),
                      summary=f"{len(out)} match{'es' * (len(out) != 1)}")


def _compact(row: dict, result: dict, lean: bool = False) -> dict:
    """What the orchestrator sees per document. Lean mode (small free-tier budgets) drops prose."""
    a = analyst.clean(result)
    return {k: v for k, v in {
        "document_id": row["id"], "name": row["name"], "path": None if lean else row["path"],
        "kind": a["document_kind"], "employment_related": a["is_employment_related"],
        "language": a.get("language_name"), "scanned": a.get("is_scanned"),
        "title": None if lean else a.get("title"), "summary": None if lean else a.get("summary"),
        "employee": a.get("employee_name"), "contract_type": a.get("contract_type"),
        "start": a.get("start_date"), "end": a.get("end_date"), "auto_renewal": a.get("auto_renewal"),
        "notice_days": a.get("notice_period_days"), "notice_deadline": a.get("notice_deadline"),
        "probation_end": a.get("probation_end_date"), "pay": a.get("compensation_text"),
        "amends": a.get("amends_document"), "signed": a.get("signed_status"),
        "risks": [f"[{r['severity']}] {r['title']}" for r in result.get("risk_flags", [])],
        "suggested_action": a.get("recommended_action"),
    }.items() if v not in (None, "", [])}


async def analyze_documents(ctx: RunContext, a: AnalyzeIn) -> ToolOutput:
    ids = list(dict.fromkeys(a.document_ids))
    rows, unknown = [], []
    for i in ids:
        r = db.one("SELECT * FROM documents WHERE id=?", (i,))
        (rows if r else unknown).append(r or i)
    sem = ctx.state.setdefault("analyst_sem", asyncio.Semaphore(ctx.settings.analyst_concurrency))
    for r in rows:
        ctx.emit("doc.status", document_id=r["id"], name=r["name"], path=r["path"], status="queued", detail="Queued")

    async def one(row: dict):
        async with sem:
            ctx.check()
            try:
                return row, await analyst.analyze(ctx, row), None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                db.update("documents", row["id"], {"status": "error", "error": str(e)[:500]})
                ctx.emit("doc.status", document_id=row["id"], name=row["name"], path=row["path"], status="error",
                         detail=str(e)[:160])
                return row, None, str(e)

    results = await asyncio.gather(*(one(r) for r in rows))
    changed("documents", "overview")
    lean = bool(ctx.settings.compat and ctx.settings.compat.tpm)
    payload = [_compact(r, res, lean) if res else {"document_id": r["id"], "name": r["name"], "error": err[:160]}
               for r, res, err in results]
    if unknown:
        known = [f"{d['id']} = {d['name']}" for d in db.query("SELECT id, name FROM documents ORDER BY name")]
        payload += [{"document_id": u, "error": "unknown document id; valid ids: " + "; ".join(known)} for u in unknown]
    ok = sum(1 for _, res, _ in results if res)
    emp = sum(1 for _, res, _ in results if res and res.get("is_employment_related"))
    return ToolOutput(content=json.dumps(payload, ensure_ascii=False),
                      summary=f"Read {ok}/{len(ids)} documents · {emp} employment-related",
                      is_error=ok == 0 and bool(ids))


async def skip_documents(ctx: RunContext, a: SkipIn) -> ToolOutput:
    done = []
    for item in a.items:
        r = db.one("SELECT * FROM documents WHERE id=?", (item.document_id,))
        if not r or r["status"] == "analyzed":
            continue
        db.update("documents", r["id"], {"status": "skipped", "skip_reason": item.reason})
        ctx.emit("doc.status", document_id=r["id"], name=r["name"], path=r["path"], status="skipped",
                 detail=item.reason)
        done.append(r["name"])
    changed("documents")
    return ToolOutput(content=f"Skipped {len(done)}: {', '.join(done)}", summary=f"Set aside {len(done)} files")


async def get_document_analysis(ctx: RunContext, a: GetAnalysisIn) -> ToolOutput:
    r = db.one("SELECT name, path, analysis_json FROM documents WHERE id=?", (a.document_id,))
    if not r or not r["analysis_json"]:
        return ToolOutput(content="No analysis for that id yet — analyse it first.", summary="Not analysed",
                          is_error=True)
    return ToolOutput(content=json.dumps({"name": r["name"], "path": r["path"],
                                          "analysis": json.loads(r["analysis_json"])}, ensure_ascii=False),
                      summary=r["name"])


def _merge_lists(items: list[list[dict]], key: str) -> list[dict]:
    seen, out = set(), []
    for lst in items:
        for it in lst or []:
            k = (it.get(key) or "").strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(it)
    return out


def _analysed_for(name: str) -> list[dict]:
    """Analysed employment documents whose worker name matches (loosely)."""
    want = {w for w in name.lower().replace("(", " ").replace(")", " ").split() if len(w) > 2}
    out = []
    for d in db.query("SELECT id, name, analysis_json FROM documents WHERE status='analyzed' AND is_employment=1"):
        emp = (json.loads(d["analysis_json"]).get("employee_name") or "").lower()
        if want and want & set(emp.replace("(", " ").replace(")", " ").split()):
            out.append(d)
    return out


def document_index() -> str:
    """Compact id ↔ name map pinned for small-context models, so ids survive context trimming."""
    rows = db.query("SELECT id, name, status, is_employment, analysis_json FROM documents ORDER BY path, name")
    lines = []
    for r in rows:
        who = ""
        if r["analysis_json"]:
            who = json.loads(r["analysis_json"]).get("employee_name") or ""
        lines.append(f"{r['id']} = {r['name']} [{r['status']}{', ' + who if who else ''}]")
    return "Document ids (use exactly these):\n" + "\n".join(lines) if lines else ""


def register(ctx: RunContext, a: RegisterIn) -> tuple[str, str]:
    primary = db.one("SELECT * FROM documents WHERE id=?", (a.primary_document_id,))
    if not primary or not primary["analysis_json"]:
        # forgive a wrong id: resolve by file name, then by the worker's analysed documents
        primary = db.one("SELECT * FROM documents WHERE name=? AND analysis_json IS NOT NULL", (a.primary_document_id,))
        if not primary:
            matches = [m for m in _analysed_for(a.employee_name)
                       if not json.loads(m["analysis_json"]).get("amends_document")]
            primary = db.one("SELECT * FROM documents WHERE id=?", (matches[0]["id"],)) if len(matches) == 1 else None
    if not primary or not primary["analysis_json"]:
        known = [f"{d['id']} ({d['name']})" for d in _analysed_for(a.employee_name)] or \
                [f"{d['id']} ({d['name']})" for d in db.query(
                    "SELECT id, name FROM documents WHERE status='analyzed' AND is_employment=1")]
        raise ValueError(f"Unknown or unanalysed primary_document_id {a.primary_document_id!r}. Analysed documents "
                         f"you can use: {', '.join(known[:12])}.")
    a.supporting_document_ids = [i for i in a.supporting_document_ids if i != primary["id"]]
    pa = json.loads(primary["analysis_json"])
    p = analyst.clean(pa)
    supporting = [d for d in (db.one("SELECT * FROM documents WHERE id=?", (i,)) for i in a.supporting_document_ids)
                  if d and d["analysis_json"]]
    sa = [json.loads(d["analysis_json"]) for d in supporting]
    emp_sa = [x for x in sa if x.get("is_employment_related")]
    email = p.get("employee_email") or next((x["employee_email"] for x in emp_sa if x.get("employee_email")), None)
    existing = db.one("SELECT id FROM contracts WHERE primary_document_id=? OR lower(employee_name)=lower(?)",
                      (primary["id"], a.employee_name))
    cid = existing["id"] if existing else db.next_id("contracts", "C")
    summary = p.get("summary") or ""
    for x in emp_sa:
        if x.get("amends_document"):
            summary += f" Amended: {x.get('summary', '')}"
    deadline, deadline_reason = a.action_deadline, a.action_deadline_reason
    if not deadline:
        # the agent left it out: derive it from the documents (amendments win over the original)
        t = today().isoformat()
        later = [analyst.clean(x) for x in emp_sa if x.get("amends_document")]
        for src in [*later, p]:
            dates = [x for x in (src.get("notice_deadline"), src.get("probation_end_date"), src.get("end_date")) if x]
            upcoming = sorted(x for x in dates if x >= t)
            if upcoming or src.get("notice_deadline"):
                deadline = upcoming[0] if upcoming else src["notice_deadline"]
                deadline_reason = deadline_reason or "Derived from the contract's notice and term dates."
                break
    row = {
        "id": cid, "employee_name": a.employee_name, "employee_email": email,
        "job_title": p.get("job_title"), "department": p.get("department"),
        "employer_entity": p.get("employer_entity"), "country": (p.get("country") or "").upper() or None,
        "work_location": p.get("work_location"), "governing_law": p.get("governing_law"),
        "language": p.get("language"), "language_name": p.get("language_name"),
        "contract_type": p.get("contract_type"), "start_date": p.get("start_date"),
        "end_date": a.effective_end_date or p.get("end_date"), "auto_renewal": int(bool(p.get("auto_renewal"))),
        "renewal_terms": p.get("renewal_terms"), "notice_period_days": p.get("notice_period_days"),
        "notice_terms": p.get("notice_terms"), "action_deadline": deadline or None,
        "action_deadline_reason": deadline_reason or None,
        "probation_end_date": p.get("probation_end_date"),
        "compensation_text": a.current_compensation or p.get("compensation_text"),
        "compensation_amount": p.get("compensation_amount"), "compensation_currency": p.get("compensation_currency"),
        "compensation_period": p.get("compensation_period"), "non_compete": p.get("non_compete"),
        "termination_terms": p.get("termination_terms"), "summary": summary.strip(),
        "key_dates_json": json.dumps(_merge_lists([pa.get("key_dates")] + [x.get("key_dates") for x in emp_sa],
                                                  "label"), ensure_ascii=False),
        "key_clauses_json": json.dumps(_merge_lists([pa.get("key_clauses")] + [x.get("key_clauses") for x in emp_sa],
                                                    "heading"), ensure_ascii=False),
        "risk_flags_json": json.dumps(_merge_lists([pa.get("risk_flags")] + [x.get("risk_flags") for x in emp_sa],
                                                   "title"), ensure_ascii=False),
        "recommended_action": a.recommended_action, "recommendation_rationale": a.recommendation_rationale,
        "confidence": p.get("confidence"), "status_note": a.status_note or None,
        "signed": {"fully_signed": 1, "partially_signed": 0, "unsigned": 0}.get(pa.get("signed_status") or "", None),
        "primary_document_id": primary["id"],
        "supporting_document_ids": json.dumps([d["id"] for d in supporting]),
        "created_at": db.now_iso(), "updated_at": db.now_iso(), "run_id": ctx.run_id,
    }
    db.upsert("contracts", row)
    alerts = deadlines.schedule_deadline_alerts(cid, ctx.run_id)
    db.log_activity(cid, "registered", f"Analysed and registered from {primary['name']}"
                    + (f" + {len(supporting)} related document(s)" if supporting else ""))
    clauses = " ".join(f"{c.get('heading')}: {c.get('english_text')}" for c in json.loads(row["key_clauses_json"]))
    risks = " ".join(f"{r.get('title')}: {r.get('detail')}" for r in json.loads(row["risk_flags_json"]))
    db.kb_index("contract", cid, f"{a.employee_name} — {row['job_title'] or ''}",
                " ".join(str(x) for x in [row["summary"], row["employer_entity"], row["country"], row["work_location"],
                                          row["governing_law"], row["contract_type"], row["renewal_terms"],
                                          row["notice_terms"], row["non_compete"], row["termination_terms"],
                                          row["compensation_text"], clauses, risks, a.recommendation_rationale,
                                          a.action_deadline_reason, a.status_note] if x))
    label, days = deadlines.urgency(row["action_deadline"])
    ctx.emit("contract.registered", contract_id=cid, employee_name=a.employee_name, urgency=label, days_left=days,
             action_deadline=row["action_deadline"], alerts=alerts)
    changed("contracts", "overview", "reminders")
    when = f"{row['action_deadline']} ({label}, {days} days)" if days is not None else "none"
    return cid, f"Registered {cid} for {a.employee_name}. Action deadline: {when}. {alerts} deadline alert(s) set."


async def register_contract(ctx: RunContext, a: RegisterIn) -> ToolOutput:
    cid, msg = register(ctx, a)
    stored = db.one("SELECT action_deadline FROM contracts WHERE id=?", (cid,)) or {}
    label, days = deadlines.urgency(stored.get("action_deadline"))
    tail = f"{days} days" if days is not None and days >= 0 else ("missed" if days is not None else "no deadline")
    return ToolOutput(content=msg, summary=f"{cid} · {a.employee_name} · {tail}")


async def flag_issue(ctx: RunContext, a: IssueIn) -> ToolOutput:
    c = db.one("SELECT id FROM contracts WHERE lower(employee_name) LIKE lower(?)", (f"%{a.employee_name}%",)) \
        if a.employee_name else None
    db.insert("issues", {"contract_id": c["id"] if c else None, "severity": a.severity, "title": a.title,
                         "detail": a.detail, "source": "agent", "created_at": db.now_iso(), "run_id": ctx.run_id})
    if c:
        db.log_activity(c["id"], "issue", a.title)
    changed("issues", "overview")
    return ToolOutput(content="Issue recorded.", summary=f"[{a.severity}] {a.title}")


async def publish_briefing(ctx: RunContext, a: BriefingIn) -> ToolOutput:
    db.insert("briefings", {"run_id": ctx.run_id, "markdown": a.markdown, "created_at": db.now_iso()})
    db.kb_index("briefing", ctx.run_id, "Handover briefing", a.markdown)
    ctx.emit("briefing.published", markdown=a.markdown)
    changed("briefing", "overview")
    return ToolOutput(content="Briefing published to counsel's dashboard.", summary="Briefing published")


def discovery_tools() -> list[ToolSpec]:
    return [
        ToolSpec("list_drive_folder", "List the files and sub-folders in a folder of the connected document store. "
                 "Call it with folder_id \"root\" first, then on every folder that could hold employment documents.",
                 ListFolderIn, list_drive_folder,
                 lambda i: ("Mapping the whole Drive" if i.get("recursive") and i.get("folder_id", "root") in ("", "root", "/")
                            else f"{'Mapping' if i.get('recursive') else 'Opening'} folder {i.get('folder_id') or 'root'}")),
        ToolSpec("search_drive", "Full-text search across file names and contents. Use it to catch documents that "
                 "are misfiled or badly named (e.g. 'agreement', 'contract', 'Arbeitsvertrag').",
                 SearchIn, search_drive, lambda i: f"Searching for “{i.get('query', '')}”"),
        ToolSpec("analyze_documents", "Have specialist analysts read documents in parallel (any language, typed or "
                 "scanned) and return structured terms, deadlines, risks and a suggested action for each. Pass "
                 "every relevant id in one call.", AnalyzeIn, analyze_documents,
                 lambda i: f"Reading {len(i.get('document_ids') or [])} documents in parallel"),
        ToolSpec("skip_documents", "Mark files as out of scope so counsel can see what was set aside and why.",
                 SkipIn, skip_documents, lambda i: f"Setting aside {len(i.get('items') or [])} unrelated files"),
        ToolSpec("get_document_analysis", "Fetch the full analysis of one document (all clauses, dates and risks).",
                 GetAnalysisIn, get_document_analysis, lambda i: "Re-reading an analysis in full"),
        ToolSpec("register_contract", "Register one worker's contract on counsel's dashboard after reconciling all "
                 "of their documents. This also schedules deadline alerts to counsel.",
                 RegisterIn, register_contract, lambda i: f"Registering {i.get('employee_name', 'contract')}"),
        ToolSpec("flag_issue", "Record a problem counsel should see (cross-document, process or portfolio level).",
                 IssueIn, flag_issue, lambda i: f"Flagging: {i.get('title', '')}"),
        ToolSpec("publish_briefing", "Publish the final briefing memo to counsel. Call once, at the end.",
                 BriefingIn, publish_briefing, lambda i: "Writing the briefing for counsel"),
    ]


def finalize(ctx: RunContext) -> str:
    """Safety net: any employment document the agent read but did not register gets
    its own contract entry, so nothing silently falls off the dashboard."""
    registered = {r["primary_document_id"] for r in db.query("SELECT primary_document_id FROM contracts")}
    for r in db.query("SELECT supporting_document_ids FROM contracts"):
        registered |= set(db.loads(r["supporting_document_ids"], []))
    for d in db.query("SELECT * FROM documents WHERE status='analyzed' AND is_employment=1"):
        if d["id"] in registered:
            continue
        a = analyst.clean(json.loads(d["analysis_json"]))
        if not a.get("employee_name") or a.get("amends_document"):
            continue
        # next date that still matters: the earliest future one; a passed notice date is kept as "missed"
        t = today().isoformat()
        dates = [x for x in (a.get("notice_deadline"), a.get("probation_end_date"), a.get("end_date")) if x]
        upcoming = sorted(x for x in dates if x >= t)
        deadline = upcoming[0] if upcoming else (a.get("notice_deadline") or "")
        register(ctx, RegisterIn(
            employee_name=a["employee_name"], primary_document_id=d["id"], effective_end_date=a.get("end_date") or "",
            action_deadline=deadline,
            action_deadline_reason="Auto-registered from the document's own dates (not reconciled by the agent).",
            recommended_action=a.get("recommended_action") or "review",
            recommendation_rationale=a.get("recommendation_rationale") or ""))
    n = db.scalar("SELECT COUNT(*) FROM contracts") or 0
    urgent = sum(1 for c in db.query("SELECT action_deadline FROM contracts")
                 if deadlines.urgency(c["action_deadline"])[0] in ("missed", "critical"))
    docs = db.scalar("SELECT COUNT(*) FROM documents WHERE status='analyzed'") or 0
    changed("contracts", "overview", "documents")
    return f"Read {docs} documents, registered {n} contracts, {urgent} need action within two weeks."


async def run(ctx: RunContext) -> str:
    from . import simulator

    drive = get_drive()
    ctx.state["drive"] = drive
    ctx.state["pinned_context"] = document_index    # re-rendered every turn for small-context providers
    tools = discovery_tools()
    s = ctx.settings
    if ctx.mode == "live":
        t = today()
        await run_agent(ctx, system=DISCOVERY_SYSTEM.format(company=s.company_name, counsel=s.counsel_name),
                        user=DISCOVERY_USER.format(today=t.isoformat(), weekday=t.strftime("%A"), source=drive.label),
                        tools=tools, max_turns=40)
    else:
        await simulator.discovery(ctx, tools)
    return finalize(ctx)
