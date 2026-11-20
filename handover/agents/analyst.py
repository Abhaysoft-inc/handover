"""The document analyst sub-agent: one structured Claude call per document.

The orchestrator fans these out in parallel; each reads the original file
natively (PDF text + page images), so scanned and non-English contracts need no
OCR pipeline."""

from __future__ import annotations

import asyncio
import json
import random

from .. import db
from ..config import GROUND_TRUTH, today
from ..connectors.drive import get_drive
from ..documents import claude_blocks, extract_text
from .prompts import ANALYST_SYSTEM, ANALYST_TASK
from .runtime import RunContext, structured_call
from .schemas import ANALYSIS_SCHEMA

_truth: dict | None = None


def ground_truth() -> dict:
    global _truth
    if _truth is None:
        _truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8")) if GROUND_TRUTH.exists() else {}
    return _truth


def from_truth(t: dict) -> dict:
    """Convert a ground-truth record into the analyst's output shape."""
    comp = t.get("compensation") or {}
    signed = t.get("signed")
    nd = t.get("notice_period_days")
    return {
        "document_kind": t.get("document_kind", "other"),
        "is_employment_related": bool(t.get("is_employment_related")),
        "language": t.get("language", "en"), "language_name": t.get("language_name", "English"),
        "is_scanned": bool(t.get("is_scanned")), "title": t.get("title", ""), "summary": t.get("summary", ""),
        **{k: t.get(k) or "" for k in ("employee_name", "employee_email", "job_title", "department",
                                        "employer_entity", "country", "work_location", "governing_law",
                                        "start_date", "end_date", "renewal_terms", "notice_terms",
                                        "notice_deadline", "probation_end_date", "non_compete",
                                        "termination_terms", "amends_document", "recommendation_rationale")},
        "contract_type": t.get("contract_type") or "not_applicable",
        "auto_renewal": bool(t.get("auto_renewal")),
        "notice_period_days": nd if isinstance(nd, int) else -1,
        "compensation_amount": comp.get("amount") or 0, "compensation_currency": comp.get("currency") or "",
        "compensation_period": comp.get("period") or "unknown", "compensation_text": comp.get("text") or "",
        "key_dates": t.get("key_dates") or [], "key_clauses": t.get("key_clauses") or [],
        "risk_flags": t.get("risk_flags") or [],
        "recommended_action": t.get("recommended_action") or "no_action",
        "confidence": t.get("confidence", "high"),
        "signed_status": "fully_signed" if signed is True else ("partially_signed" if signed is False else "unclear"),
    }


def clean(a: dict) -> dict:
    """Analyst output → Python values (empty / -1 / 0 become None)."""
    out = dict(a)
    for k, v in a.items():
        if isinstance(v, str) and not v.strip():
            out[k] = None
    if out.get("notice_period_days") in (-1, None):
        out["notice_period_days"] = None
    if not out.get("compensation_amount"):
        out["compensation_amount"] = None
    return out


def _simulated(doc: dict) -> dict:
    truth = ground_truth().get("documents", {}).get(doc["source_ref"])
    if truth:
        return from_truth(truth)
    return from_truth({"document_kind": "other", "is_employment_related": False, "title": doc["name"],
                       "summary": "Simulation mode only knows the bundled demo documents. Add an Anthropic API "
                                  "key to analyse your own files with Claude."})


async def analyze(ctx: RunContext, doc: dict) -> dict:
    drive = ctx.state.get("drive") or get_drive()
    did = doc["id"]
    if doc.get("analysis_json") and doc.get("status") == "analyzed":
        # already read in an earlier run: reuse it (re-runs only spend model calls on new or failed files)
        result = json.loads(doc["analysis_json"])
        ctx.emit("doc.status", document_id=did, name=doc["name"], path=doc["path"], status="analyzed",
                 detail="from earlier run · " + (doc.get("kind") or "").replace("_", " "),
                 employment=bool(doc.get("is_employment")), language=doc.get("language"),
                 scanned=bool(doc.get("is_scanned")))
        return result
    ctx.emit("doc.status", document_id=did, name=doc["name"], path=doc["path"], status="reading", detail="Opening")
    db.update("documents", did, {"status": "analyzing", "run_id": ctx.run_id})
    data, mime = await drive.download(doc["source_ref"])
    text, pages, scanned = await asyncio.to_thread(extract_text, data, mime)
    db.update("documents", did, {"text_content": text[:200_000], "pages": pages, "mime_type": mime})
    ctx.emit("doc.status", document_id=did, name=doc["name"], path=doc["path"], status="reading",
             detail=f"{pages or 1} page{'s' if (pages or 1) != 1 else ''}" + (" · image-only scan" if scanned else ""))
    if ctx.mode == "live":
        content = [*claude_blocks(data, mime, doc["name"]),
                   {"type": "text", "text": ANALYST_TASK.format(name=doc["name"], path=doc["path"],
                                                                today=today().isoformat())}]
        result = await structured_call(ctx, system=ANALYST_SYSTEM.format(company=ctx.settings.company_name),
                                       content=content, schema=ANALYSIS_SCHEMA)
    else:
        await asyncio.sleep(random.uniform(0.9, 2.4) + (1.2 if scanned else 0))
        result = _simulated(doc)

    a = clean(result)
    db.update("documents", did, {
        "status": "analyzed", "kind": a["document_kind"], "is_employment": int(bool(a["is_employment_related"])),
        "language": a.get("language"), "language_name": a.get("language_name"),
        "is_scanned": int(bool(a.get("is_scanned") or scanned)), "title": a.get("title"),
        "summary": a.get("summary"), "analysis_json": json.dumps(result, ensure_ascii=False),
        "analyzed_at": db.now_iso(), "error": None})
    clauses = " ".join(f"{c.get('heading')}: {c.get('english_text')} {c.get('original_text')}"
                       for c in result.get("key_clauses", []))
    risks = " ".join(f"{r.get('title')}: {r.get('detail')}" for r in result.get("risk_flags", []))
    db.kb_index("document", did, a.get("title") or doc["name"],
                f"{doc['path']}/{doc['name']} {a.get('summary') or ''} {clauses} {risks} {text[:20000]}")
    detail = " · ".join(x for x in [
        (a.get("document_kind") or "").replace("_", " "), a.get("language_name"),
        "scanned" if a.get("is_scanned") else None, a.get("employee_name")] if x)
    ctx.emit("doc.status", document_id=did, name=doc["name"], path=doc["path"], status="analyzed", detail=detail,
             employment=bool(a["is_employment_related"]), language=a.get("language"),
             scanned=bool(a.get("is_scanned")))
    return result
