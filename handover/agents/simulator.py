"""Simulation mode: a scripted "brain" that drives the *real* tools.

Used when no API key is configured (or HANDOVER_MODE=simulate). Every tool call
below goes through the same code path the live Claude agent uses, so the
database, dashboard, drafts, outbox, reminders and filings all behave exactly as
in a live run; only the decisions and the language work are canned (from
sample_data/ground_truth.json and templates)."""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import timedelta

from .. import db, deadlines
from ..config import get_settings, today
from ..connectors.drive import doc_id
from .analyst import ground_truth
from .runtime import RunContext, ToolSpec, call_tool

COUNTRY_NAMES = {"US": "United States", "DE": "Germany", "ES": "Spain", "FR": "France", "IN": "India",
                 "JP": "Japan", "GB": "United Kingdom"}


async def _pause(lo: float = 0.35, hi: float = 0.9) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _note(ctx: RunContext, text: str) -> None:
    ctx.emit("agent.note", kind="update", text=text)


# ── discovery ─────────────────────────────────────────────────────────────


async def discovery(ctx: RunContext, tools: list[ToolSpec]) -> None:
    _note(ctx, "Mapping the whole Drive first, then sending every plausible employment document to the analysts "
               "in one parallel batch.")
    await _pause()
    queue, files = ["root"], []
    while queue:
        ctx.check()
        res = await call_tool(ctx, tools, "list_drive_folder", {"folder_id": queue.pop(0)})
        for it in json.loads(res["content"])["items"]:
            (queue.append(it["folder_id"]) if it["type"] == "folder" else files.append(it))
        await _pause(0.2, 0.5)
    _note(ctx, f"Found {len(files)} files across the Drive. 'Scans/scan_0042.pdf' has no meaningful name, so I'll "
               "check what full-text search can see.")
    await call_tool(ctx, tools, "search_drive", {"query": "Arbeitsvertrag"})
    await _pause()
    _note(ctx, "Search can't see inside image-only scans, so the scan goes to the analysts unopened. Invoices, "
               "vendor NDAs and leases are out of scope.")
    skip_reasons = {"invoice": "Vendor invoice — finance, not employment",
                    "nda": "Vendor NDA — not an employment agreement",
                    "lease": "Office lease — real estate (deadline noted separately)"}
    truth = ground_truth().get("documents", {})
    rows = {d["id"]: d for d in db.query("SELECT * FROM documents")}
    skip, read = [], []
    for f in files:
        kind = truth.get(rows[f["document_id"]]["source_ref"], {}).get("document_kind")
        (skip if kind in skip_reasons else read).append((f, kind))
    await call_tool(ctx, tools, "skip_documents",
                    {"items": [{"document_id": f["document_id"], "reason": skip_reasons[k]} for f, k in skip]})
    await _pause()
    await call_tool(ctx, tools, "analyze_documents", {"document_ids": [f["document_id"] for f, _ in read]})

    _note(ctx, "Reconciling: Maya Chen's original agreement ended in the summer, but Amendment No. 1 extends it, so "
               "her deadline moves. Arjun Sharma's 30-day non-renewal window has already closed. Board minutes add "
               "context on the VP Sales renewal and an EMEA hiring freeze.")
    await _pause(0.8, 1.4)
    for c in ground_truth().get("contracts", []):
        ctx.check()
        primary = db.one("SELECT * FROM documents WHERE id=?", (doc_id("local", c["primary"]),))
        if not primary:
            continue
        pt = truth.get(c["primary"], {})
        rec, why = pt.get("recommended_action"), pt.get("recommendation_rationale", "")
        if rec == "review" and c.get("supporting"):        # amendment supersedes the original's advice
            for sp in c["supporting"]:
                st = truth.get(sp, {})
                if st.get("is_employment_related"):
                    rec, why = st.get("recommended_action", rec), st.get("recommendation_rationale", why)
        await call_tool(ctx, tools, "register_contract", {
            "employee_name": c["employee_name"], "primary_document_id": primary["id"],
            "supporting_document_ids": [doc_id("local", p) for p in c.get("supporting", [])],
            "effective_end_date": c.get("effective_end_date") or "", "action_deadline": c.get("action_deadline") or "",
            "action_deadline_reason": c.get("action_deadline_reason") or "",
            "current_compensation": c.get("current_compensation_text") or "",
            "recommended_action": rec or "review", "recommendation_rationale": why,
            "status_note": c.get("status_note") or ""})
        await _pause(0.15, 0.4)
    for issue in ground_truth().get("issues", []):
        await call_tool(ctx, tools, "flag_issue", {k: issue.get(k) or "" for k in
                                                   ("severity", "title", "detail", "employee_name")})
        await _pause(0.1, 0.3)
    await _pause()
    await call_tool(ctx, tools, "publish_briefing", {"markdown": build_briefing()})


def build_briefing() -> str:
    t = today()
    contracts = db.query("SELECT * FROM contracts")
    countries = sorted({c["country"] for c in contracts if c["country"]})
    langs = sorted({c["language_name"] for c in contracts if c["language_name"]})
    dated = []
    for c in contracts:
        label, days = deadlines.urgency(c["action_deadline"], t)
        if days is not None:
            dated.append((c["action_deadline"], days, label, c))
    dated.sort(key=lambda x: x[0])
    urgent = [x for x in dated if x[2] in ("missed", "critical")]
    lines = [f"**Bottom line:** you inherited **{len(contracts)} worker contracts** across "
             f"{len(countries)} countries in {len(langs)} languages. **{len(urgent)} need a decision within two "
             f"weeks** and {sum(1 for x in dated if x[2] == 'missed')} notice window has already been missed.", "",
             "### Deadlines"]
    for d, days, label, c in dated:
        when = f"{-days} days ago" if days < 0 else ("today" if days == 0 else f"in {days} days")
        lines.append(f"- **{d}** ({when}) — **{c['employee_name']}** [{c['id']}]: {c['action_deadline_reason']}")
    risks = []
    for c in contracts:
        for r in db.loads(c["risk_flags_json"], []):
            if r.get("severity") == "high":
                risks.append(f"- **{c['employee_name']}** [{c['id']}] — {r['title']}. {r['detail']}")
    lines += ["", "### Biggest legal risks", *risks[:6], "", "### Recommended next steps"]
    if urgent:
        lines.append("1. Decide this week on " + ", ".join(f"{x[3]['employee_name']} [{x[3]['id']}]" for x in urgent)
                     + " — assign actions on the Portfolio tab and Handover drafts the documents.")
    lines += ["2. Fix the compliance gaps that don't wait for a deadline (void non-compete, unsigned CDD, "
              "misclassification).",
              "3. Approve drafts, then let Handover send, file and chase signatures; deadline alerts are already "
              "scheduled to your inbox."]
    return "\n".join(lines)


# ── drafting ──────────────────────────────────────────────────────────────


def _fmt(d: str | None) -> str:
    p = deadlines.parse(d)
    return f"{p.strftime('%B')} {p.day}, {p.year}" if p else "[date]"


def _compliance(c: dict, action: str) -> tuple[list[str], str]:
    country, law = (c.get("country") or "").upper(), (c.get("governing_law") or "")
    notes, method = [], "email"
    if country == "DE":
        notes.append("German law: a termination must be in wet-ink written form (§ 623 BGB); e-mail or PDF alone is "
                     "void. Contract changes should also be signed on paper.")
        method = "email_and_wet_ink"
        if action in ("non_renew", "terminate"):
            notes.append("Waive the post-contractual non-compete in writing before employment ends, or "
                         "compensation of 50% of pay is owed for 12 months (§ 75a HGB).")
    if country == "FR":
        notes.append("France: renewal of a CDD requires an avenant signed before the term ends; the current CDD lacks "
                     "the employee's signature (requalification risk).")
        method = "email_and_wet_ink"
    if country == "ES":
        notes.append("Spain: give at least 15 days' written notice before expiry (art. 49.1.c ET); the 18-month term "
                     "may already be treated as indefinite.")
    if country == "IN" and action == "terminate":
        notes.append("Termination for convenience needs 60 days' written notice under clause 4; consider the "
                     "misclassification exposure (PF, gratuity) before the exit.")
    if country == "JP":
        notes.append("Japan: non-renewal needs 30 days' advance notice and objectively reasonable grounds after "
                     "repeated renewals (Labour Contract Act § 19).")
    if country == "US" and "California" in law and action == "amend":
        notes.append("California: Bus. & Prof. Code § 16600.1 requires written notice to the employee that the "
                     "non-compete is void; include it with the amendment.")
    if action in ("non_renew", "terminate"):
        notes.append("Company policy: separations need CEO and Head of People approval and Legal review before "
                     "sending (Employee Handbook § 7).")
    return notes, method


def draft(c: dict, action: str, params: dict) -> dict:
    s = get_settings()
    t = today()
    name, entity = c["employee_name"], c.get("employer_entity") or s.company_name
    first = name.split()[0]
    lang = c.get("language_name") or "English"
    note = (f"\n\n> *Simulation mode drafts in English. In live mode the AI drafts this document in {lang}, "
            f"following local formalities.*") if lang != "English" else ""
    ref = f"the {c.get('job_title') or 'employment'} agreement between {entity} and {name}"
    effective = params.get("effective_date") or c.get("end_date") or (t + timedelta(days=60)).isoformat()
    two_party = action in ("renew", "convert_to_permanent", "amend")
    sig = (f"\n\n**For {entity}**\n\n______________________________\n{s.counsel_name}, Legal")
    if two_party:
        sig += f"\n\n**Acknowledged and agreed**\n\n______________________________\n{name}\n\nDate: ____________"
    notes, method = _compliance(c, action)
    doc_type, title, body, summary = "other", "Letter", "", ""
    if action == "renew":
        new_end = params.get("new_end_date") or (deadlines.parse(c.get("end_date")) or t).replace(
            year=(deadlines.parse(c.get("end_date")) or t).year + 1).isoformat()
        pay = f" The base compensation becomes {params['new_salary']}." if params.get("new_salary") else ""
        doc_type, title = "renewal_addendum", "Renewal Addendum"
        body = (f"# Renewal Addendum\n\n**Parties:** {entity} (the \"Company\") and {name}.\n\n"
                f"This Addendum amends {ref}.\n\n## 1. Extension of the Term\nThe term is extended until "
                f"**{_fmt(new_end)}**.{pay}\n\n## 2. Other Terms\nAll other terms remain unchanged and in full force."
                f"{sig}{note}")
        summary = f"Extends {name}'s contract to {new_end}.{pay} All other terms unchanged."
    elif action == "non_renew":
        doc_type, title = "non_renewal_notice", "Notice of Non-Renewal"
        body = (f"# Notice of Non-Renewal\n\n{_fmt(t.isoformat())}\n\nDear {first},\n\nIn accordance with {ref}, "
                f"{entity} gives notice that the agreement will **not be renewed** and will end on "
                f"**{_fmt(c.get('end_date'))}**.\n\nWe thank you for your contributions. HR will contact you about "
                f"the handover, final pay and any entitlements.{sig}{note}")
        summary = f"Gives {name} notice that the contract ends on {c.get('end_date')} without renewal."
    elif action == "terminate":
        doc_type, title = "termination_letter", "Notice of Termination"
        reason = params.get("reason") or "business reasons"
        body = (f"# Notice of Termination\n\n{_fmt(t.isoformat())}\n\nDear {first},\n\nWe write to give you notice "
                f"that {entity} is terminating {ref} for {reason}, under its termination provisions "
                f"({c.get('termination_terms') or 'as set out in the agreement'}).\n\nThe termination takes effect on "
                f"**{_fmt(effective)}**. Until then the agreement continues as normal.{sig}{note}")
        summary = f"Terminates {name}'s agreement effective {effective} ({reason})."
    elif action == "convert_to_permanent":
        doc_type, title = "conversion_addendum", "Addendum: Conversion to Indefinite Employment"
        body = (f"# Addendum: Conversion to Indefinite Employment\n\n**Parties:** {entity} and {name}.\n\nWith effect "
                f"from **{_fmt(params.get('effective_date') or c.get('end_date'))}**, the fixed term in {ref} is "
                f"removed and the employment continues for an **indefinite period**. All other terms remain "
                f"unchanged.{sig}{note}")
        summary = f"Converts {name}'s fixed-term contract into indefinite employment."
        notes.append("Sign before the current fixed term ends.")
    elif action == "confirm_probation":
        doc_type, title = "probation_confirmation", "Confirmation of Employment"
        body = (f"# Confirmation of Employment\n\n{_fmt(t.isoformat())}\n\nDear {first},\n\nWe are pleased to "
                f"confirm that you have successfully completed your probation. Your employment as "
                f"{c.get('job_title') or 'a member of the team'} is confirmed. From today the post-confirmation "
                f"notice period in your offer letter applies.\n\nCongratulations, and thank you.{sig}")
        summary = f"Confirms {name}'s employment after probation; post-confirmation notice applies."
        notes.append("Issue before the probation end date; afterwards confirmation is implied.")
    elif action == "amend":
        doc_type, title = "contract_amendment", "Contract Amendment"
        changes = params.get("changes") or "Clause [X] is deleted in its entirety."
        body = (f"# Amendment to Employment Agreement\n\n**Parties:** {entity} and {name}.\n\nThis Amendment amends "
                f"{ref}, effective **{_fmt(t.isoformat())}**.\n\n## 1. Changes\n{changes}\n\n## 2. Other Terms\n"
                f"All other terms remain in full force.{sig}{note}")
        summary = f"Amends {name}'s agreement: {changes}"
    if params.get("notes"):
        body += f"\n\n*Counsel's note: {params['notes']}*"
    hr = s.hr_email
    to = [c["employee_email"]] if c.get("employee_email") else []
    return {
        "doc_type": doc_type, "title": title, "language": "en", "body_markdown": body,
        "english_summary": summary + " (Simulation draft.)", "compliance_notes": notes, "delivery_method": method,
        "recipients_to": to, "recipients_cc": [hr],
        "cover_email_subject": f"{title} — {name}",
        "cover_email_body": (f"Dear {first},\n\nPlease find attached your {title.lower()}. "
                             + ("Please sign and return it at your earliest convenience. " if two_party else "")
                             + f"If you have any questions, reply to this email or contact HR at {hr}.\n\n"
                               f"Kind regards,\n{s.counsel_name}\nLegal, {s.company_name}"),
        "signature_required_from": [name, entity] if two_party else [entity],
        "sign_by_date": (c.get("action_deadline") if (deadlines.parse(c.get("action_deadline")) or t) >= t else ""),
    }


# ── execution ─────────────────────────────────────────────────────────────


async def execution(ctx: RunContext, tools: list[ToolSpec], drafts: list[dict]) -> None:
    s = get_settings()
    t = today()
    _note(ctx, f"Executing {len(drafts)} approved document(s): deliver, schedule follow-ups, then file the finals.")
    await call_tool(ctx, tools, "list_approved_documents", {})
    for d in drafts:
        ctx.check()
        c = db.one("SELECT * FROM contracts WHERE id=?", (d["contract_id"],))
        delivery = db.loads(d["delivery_json"], {})
        to = [c["employee_email"]] if c.get("employee_email") else [s.hr_email]
        await _pause()
        await call_tool(ctx, tools, "send_document_email", {
            "draft_id": d["id"], "to": to, "cc": [s.hr_email],
            "subject": delivery.get("subject") or d["title"], "body": delivery.get("body") or d["english_summary"]})
        if delivery.get("method") in ("email_and_wet_ink", "wet_ink_only"):
            await call_tool(ctx, tools, "schedule_reminder", {
                "contract_id": c["id"], "draft_id": d["id"], "kind": "delivery",
                "due_date": (t + timedelta(days=2)).isoformat(), "recipients": [s.hr_email],
                "subject": f"Hand-deliver the wet-ink original: {d['title']} ({c['employee_name']})",
                "message": "The emailed copy is a courtesy copy only. Local law requires the signed paper original; "
                           "deliver it and upload the countersigned scan."})
        if len(delivery.get("signature_required_from") or []) > 1:
            await call_tool(ctx, tools, "schedule_reminder", {
                "contract_id": c["id"], "draft_id": d["id"], "kind": "signature",
                "due_date": (t + timedelta(days=5)).isoformat(), "recipients": to,
                "subject": f"Reminder: please sign your {d['title'].lower()}",
                "message": f"Hi {c['employee_name'].split()[0]}, a friendly reminder to sign and return the document "
                           f"we sent on {t.isoformat()}."})
        sign_by = deadlines.parse(delivery.get("sign_by_date"))
        if sign_by and (sign_by - timedelta(days=3)) >= t:
            await call_tool(ctx, tools, "schedule_reminder", {
                "contract_id": c["id"], "draft_id": d["id"], "kind": "deadline",
                "due_date": (sign_by - timedelta(days=3)).isoformat(), "recipients": [s.counsel_email],
                "subject": f"3 days to sign-by date: {c['employee_name']}",
                "message": f"“{d['title']}” must be signed by {sign_by.isoformat()}. Check status in Handover."})
        country = c.get("country") or "XX"
        filed = await call_tool(ctx, tools, "file_to_drive", {
            "draft_id": d["id"], "folder_path": f"Legal/Employment/{country}/{c['employee_name']}"})
        tail = "final filed" if not filed.get("is_error") else "filing failed — needs attention"
        await call_tool(ctx, tools, "complete_execution", {
            "draft_id": d["id"], "summary": f"Sent to {', '.join(to)}, follow-ups scheduled, {tail}."})


# ── knowledge base answers ────────────────────────────────────────────────


async def answer(ctx: RunContext, tools: list[ToolSpec], question: str) -> str:
    q = question.lower()
    await _pause(0.3, 0.6)
    lines: list[str] = []
    if any(w in q for w in ("deadline", "expir", "renew", "due", "urgent", "next week", "this month")):
        res = await call_tool(ctx, tools, "list_deadlines", {"within_days": 120})
        rows = json.loads(res["content"])["deadlines"]
        lines.append(f"**{len(rows)} deadlines** in the next 120 days (missed ones included):")
        for r in rows:
            when = f"{-r['days_left']} days ago" if r["days_left"] < 0 else f"in {r['days_left']} days"
            lines.append(f"- **{r['deadline']}** ({when}) — {r['worker']} [{r['contract_id']}]: {r['what']}")
    elif "compete" in q:
        res = await call_tool(ctx, tools, "filter_contracts", {"has_non_compete": True})
        rows = json.loads(res["content"])
        lines.append(f"**{len(rows)} contracts** contain a post-employment non-compete:")
        for r in rows:
            lines.append(f"- {r['worker']} [{r['contract_id']}] ({r.get('country')}): {r.get('non_compete')}")
    else:
        country = next((code for code, name in COUNTRY_NAMES.items()
                        if name.lower() in q or re.search(rf"\b{code.lower()}\b", q)), "")
        if country:
            res = await call_tool(ctx, tools, "filter_contracts", {"country": country})
            rows = json.loads(res["content"])
            lines.append(f"**{len(rows)} contract(s)** in {COUNTRY_NAMES[country]}:")
            for r in rows:
                lines.append(f"- {r['worker']} [{r['contract_id']}] — {r.get('title')}, {r.get('type')}, "
                             f"deadline {r.get('action_deadline') or 'none'}")
        else:
            res = await call_tool(ctx, tools, "search_knowledge", {"query": question})
            hits = json.loads(res["content"])
            if not hits:
                return "I couldn't find anything about that in the knowledge base."
            lines.append(f"Here's what the knowledge base has on “{question}”:")
            for h in hits[:6]:
                cite = f" [{h['contract_id']}]" if h.get("contract_id") else (
                    f" [{h['id']}]" if h["type"] == "contract" else "")
                lines.append(f"- **{h['title']}**{cite}: {h['snippet']}")
    lines.append("\n*Simulation mode answers with retrieval only; add a Claude or Gemini API key for full AI reasoning.*")
    text = "\n".join(lines)
    for chunk in re.findall(r".{1,40}", text, flags=re.S):
        ctx.emit("agent.text_delta", text=chunk)
        await asyncio.sleep(0.02)
    return text
