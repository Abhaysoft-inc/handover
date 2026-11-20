"""Stage 4: draft every document counsel's decisions require, in parallel, each in
the language and legal style of the original contract."""

from __future__ import annotations

import asyncio
import json
import random

from .. import db
from ..config import DATA_DIR, today
from ..connectors.drive import get_drive
from ..documents import claude_blocks, render_docx, render_pdf, safe_filename
from ..events import changed
from .prompts import DRAFTER_SYSTEM, DRAFTER_TASK
from .runtime import RunContext, structured_call
from .schemas import DRAFT_SCHEMA

ACTION_LABELS = {
    "renew": "Renew / extend the contract",
    "non_renew": "Give notice of non-renewal (let it end at term)",
    "terminate": "Terminate the contract",
    "amend": "Amend the contract",
    "convert_to_permanent": "Convert to permanent (indefinite) employment",
    "confirm_probation": "Confirm successful completion of probation",
}
DOC_TYPE_FOR = {"renew": "renewal_addendum", "non_renew": "non_renewal_notice", "terminate": "termination_letter",
                "amend": "contract_amendment", "convert_to_permanent": "conversion_addendum",
                "confirm_probation": "probation_confirmation"}
DOC_TYPE_LABEL = {"renewal_addendum": "renewal addendum", "non_renewal_notice": "notice of non-renewal",
                  "termination_letter": "termination letter", "contract_amendment": "contract amendment",
                  "conversion_addendum": "conversion addendum", "probation_confirmation": "probation confirmation",
                  "other": "document"}


def contract_brief(c: dict) -> dict:
    keep = ["id", "employee_name", "employee_email", "job_title", "department", "employer_entity", "country",
            "work_location", "governing_law", "language_name", "contract_type", "start_date", "end_date",
            "renewal_terms", "notice_period_days", "notice_terms", "action_deadline", "action_deadline_reason",
            "probation_end_date", "compensation_text", "non_compete", "termination_terms", "summary",
            "recommended_action", "recommendation_rationale", "status_note"]
    out = {k: c[k] for k in keep if c.get(k) not in (None, "")}
    out["key_clauses"] = db.loads(c.get("key_clauses_json"), [])
    out["risk_flags"] = db.loads(c.get("risk_flags_json"), [])
    return out


def render_files(draft_id: str) -> None:
    d = db.one("SELECT * FROM drafts WHERE id=?", (draft_id,))
    c = db.one("SELECT * FROM contracts WHERE id=?", (d["contract_id"],)) or {}
    s_company = c.get("employer_entity") or "Company"
    from ..config import get_settings

    s = get_settings()
    letterhead = f"{s_company} · Legal · {s.counsel_name} · {s.counsel_email}"
    folder = DATA_DIR / "drafts" / draft_id
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_filename(f"{d['title']}_{c.get('employee_name', '')}")
    docx_path, pdf_path = folder / f"{base}.docx", folder / f"{base}.pdf"
    docx_path.write_bytes(render_docx(d["title"], d["body_markdown"]))
    try:
        pdf_path.write_bytes(render_pdf(d["title"], d["body_markdown"], letterhead))
        pdf_rel = pdf_path.relative_to(DATA_DIR).as_posix()
    except Exception:
        pdf_rel = None
    db.update("drafts", draft_id, {"docx_path": docx_path.relative_to(DATA_DIR).as_posix(), "pdf_path": pdf_rel})


def save_draft(ctx: RunContext, c: dict, decision: dict, out: dict) -> str:
    did = db.next_id("drafts", "D")
    delivery = {"method": out.get("delivery_method", "email"), "to": out.get("recipients_to") or [],
                "cc": out.get("recipients_cc") or [], "subject": out.get("cover_email_subject", ""),
                "body": out.get("cover_email_body", ""), "signature_required_from": out.get("signature_required_from") or [],
                "sign_by_date": out.get("sign_by_date") or ""}
    db.insert("drafts", {
        "id": did, "contract_id": c["id"], "decision_id": decision["id"], "doc_type": out.get("doc_type"),
        "title": out.get("title") or "Draft", "language": out.get("language") or c.get("language") or "en",
        "body_markdown": out.get("body_markdown") or "", "english_summary": out.get("english_summary") or "",
        "compliance_notes_json": json.dumps(out.get("compliance_notes") or [], ensure_ascii=False),
        "delivery_json": json.dumps(delivery, ensure_ascii=False), "status": "draft", "version": 1,
        "created_at": db.now_iso(), "updated_at": db.now_iso(), "run_id": ctx.run_id})
    try:
        render_files(did)
    except Exception:
        db.execute("DELETE FROM drafts WHERE id=?", (did,))
        raise
    db.log_activity(c["id"], "drafted", f"{out.get('title')} drafted ({did})")
    db.kb_index("draft", did, out.get("title") or did, f"{out.get('english_summary', '')} {out.get('body_markdown', '')}")
    ctx.emit("draft.created", draft_id=did, contract_id=c["id"], title=out.get("title"),
             language=out.get("language"), employee_name=c["employee_name"])
    changed("drafts", "overview", "contracts")
    return did


async def draft_one(ctx: RunContext, decision: dict) -> str:
    from . import simulator

    c = db.one("SELECT * FROM contracts WHERE id=?", (decision["contract_id"],))
    params = db.loads(decision["params_json"], {})
    db.update("decisions", decision["id"], {"status": "drafting", "updated_at": db.now_iso()})
    kind = DOC_TYPE_LABEL[DOC_TYPE_FOR.get(decision["action"], "other")]
    lang = c.get("language_name") or "English"
    label = f"Drafting {kind} for {c['employee_name']}" + (f" in {lang}" if lang != "English" else "")
    async with ctx.step("draft_document", label, {"contract_id": c["id"], "action": decision["action"]}) as step:
        if ctx.mode == "live":
            s = ctx.settings
            blocks: list = []
            primary = db.one("SELECT * FROM documents WHERE id=?", (c.get("primary_document_id"),))
            if primary:
                data, mime = await (ctx.state.get("drive") or get_drive()).download(primary["source_ref"])
                blocks = claude_blocks(data, mime, primary["name"])
            task = DRAFTER_TASK.format(today=today().isoformat(), action_label=ACTION_LABELS[decision["action"]],
                                       params=json.dumps(params, ensure_ascii=False) if params else "(none)",
                                       contract=json.dumps(contract_brief(c), ensure_ascii=False, indent=1))
            out = await structured_call(
                ctx, system=DRAFTER_SYSTEM.format(company=s.company_name, counsel=s.counsel_name,
                                                  counsel_email=s.counsel_email, hr_email=s.hr_email),
                content=[*blocks, {"type": "text", "text": task}], schema=DRAFT_SCHEMA, max_tokens=32000)
        else:
            await asyncio.sleep(random.uniform(1.5, 3.2))
            out = simulator.draft(c, decision["action"], params)
        did = save_draft(ctx, c, decision, out)
        step["summary"] = f"{did} · {out.get('title')}"
    db.update("decisions", decision["id"], {"status": "drafted", "updated_at": db.now_iso()})
    return did


async def run(ctx: RunContext) -> str:
    decisions = db.query("SELECT * FROM decisions WHERE status='pending_draft' ORDER BY id")
    if not decisions:
        return "Nothing to draft. Assign actions on the dashboard first."
    ctx.state["drive"] = get_drive()
    ctx.emit("agent.note", kind="update",
             text=f"Drafting {len(decisions)} document{'s' * (len(decisions) != 1)} in parallel, each grounded in "
                  f"the original contract and its governing law.")
    sem = asyncio.Semaphore(3)

    async def one(dec: dict):
        async with sem:
            ctx.check()
            try:
                return await draft_one(ctx, dec)
            except asyncio.CancelledError:
                raise
            except Exception:
                db.update("decisions", dec["id"], {"status": "pending_draft"})
                raise

    results = await asyncio.gather(*(one(d) for d in decisions), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    ok = len(results) - len(errors)
    if errors and not ok:
        raise errors[0]
    return f"Drafted {ok} document{'s' * (ok != 1)} for your review" + (f" ({len(errors)} failed)" if errors else ".")
