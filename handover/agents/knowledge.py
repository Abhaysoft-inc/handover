"""Stage 6: the searchable knowledge base, answered by an agent that retrieves
before it speaks and cites contracts as [C-###]."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from .. import db, deadlines
from ..config import today
from .prompts import KNOWLEDGE_SYSTEM
from .runtime import RunContext, ToolOutput, ToolSpec, run_agent


class SearchIn(BaseModel):
    query: str = Field(..., description="Keywords; clause names, people, places, legal concepts.")


class FilterIn(BaseModel):
    country: str = Field("", description="ISO country code, e.g. DE, US, IN; empty for any")
    contract_type: str = Field("", description="fixed_term | indefinite | at_will | contractor; empty for any")
    urgency: str = Field("", description="missed | critical | soon | upcoming | ok | none; empty for any")
    has_non_compete: bool | None = Field(None, description="true/false to filter on a post-employment non-compete")
    recommended_action: str = Field("", description="e.g. renew, terminate, amend; empty for any")


class ContractIn(BaseModel):
    contract_id: str


class DeadlinesIn(BaseModel):
    within_days: int = Field(90, description="Look-ahead window in days (missed deadlines are always included)")


def contract_row(c: dict) -> dict:
    label, days = deadlines.urgency(c["action_deadline"])
    return {k: v for k, v in {
        "contract_id": c["id"], "worker": c["employee_name"], "title": c["job_title"], "entity": c["employer_entity"],
        "country": c["country"], "type": c["contract_type"], "language": c["language_name"], "end": c["end_date"],
        "action_deadline": c["action_deadline"], "urgency": label, "days_left": days, "pay": c["compensation_text"],
        "non_compete": c["non_compete"], "recommended": c["recommended_action"],
    }.items() if v not in (None, "")}


async def search_knowledge(ctx: RunContext, a: SearchIn) -> ToolOutput:
    hits = db.kb_search(a.query, limit=10)
    out = []
    for h in hits:
        item = {"type": h["ref_type"], "id": h["ref_id"], "title": h["title"], "snippet": h["snippet"]}
        if h["ref_type"] == "document":
            c = db.one("SELECT id FROM contracts WHERE primary_document_id=? OR supporting_document_ids LIKE ?",
                       (h["ref_id"], f'%"{h["ref_id"]}"%'))
            if c:
                item["contract_id"] = c["id"]
        out.append(item)
    return ToolOutput(content=json.dumps(out, ensure_ascii=False), summary=f"{len(out)} hits for “{a.query}”")


async def filter_contracts(ctx: RunContext, a: FilterIn) -> ToolOutput:
    rows = []
    for c in db.query("SELECT * FROM contracts ORDER BY employee_name"):
        r = contract_row(c)
        if a.country and (c["country"] or "").upper() != a.country.upper():
            continue
        if a.contract_type and c["contract_type"] != a.contract_type:
            continue
        if a.urgency and r.get("urgency") != a.urgency:
            continue
        if a.has_non_compete is not None and bool(c["non_compete"]) != a.has_non_compete:
            continue
        if a.recommended_action and c["recommended_action"] != a.recommended_action:
            continue
        rows.append(r)
    return ToolOutput(content=json.dumps(rows, ensure_ascii=False), summary=f"{len(rows)} contracts")


async def get_contract(ctx: RunContext, a: ContractIn) -> ToolOutput:
    c = db.one("SELECT * FROM contracts WHERE id=?", (a.contract_id.upper(),))
    if not c:
        return ToolOutput(content="No contract with that id.", summary="Not found", is_error=True)
    detail = {k: v for k, v in c.items() if v not in (None, "") and not k.endswith("_json") and k not in ("run_id",)}
    detail["key_clauses"] = db.loads(c["key_clauses_json"], [])
    detail["risk_flags"] = db.loads(c["risk_flags_json"], [])
    detail["key_dates"] = db.loads(c["key_dates_json"], [])
    detail["decision"] = db.one("SELECT action, params_json, status FROM decisions WHERE contract_id=?", (c["id"],))
    detail["drafts"] = db.query("SELECT id, title, status FROM drafts WHERE contract_id=?", (c["id"],))
    detail["documents"] = [db.one("SELECT name, path FROM documents WHERE id=?", (i,))
                           for i in [c["primary_document_id"], *db.loads(c["supporting_document_ids"], [])]]
    return ToolOutput(content=json.dumps(detail, ensure_ascii=False, default=str), summary=f"{c['id']} · {c['employee_name']}")


async def list_deadlines(ctx: RunContext, a: DeadlinesIn) -> ToolOutput:
    rows = []
    for c in db.query("SELECT * FROM contracts WHERE action_deadline IS NOT NULL"):
        label, days = deadlines.urgency(c["action_deadline"])
        if days is not None and days <= a.within_days:
            rows.append({"contract_id": c["id"], "worker": c["employee_name"], "deadline": c["action_deadline"],
                         "days_left": days, "urgency": label, "what": c["action_deadline_reason"]})
    rows.sort(key=lambda r: r["deadline"])
    return ToolOutput(content=json.dumps({"today": today().isoformat(), "deadlines": rows}, ensure_ascii=False),
                      summary=f"{len(rows)} deadlines within {a.within_days} days")


def knowledge_tools() -> list[ToolSpec]:
    return [
        ToolSpec("search_knowledge", "Full-text search over every analysed document, contract record, draft and "
                 "briefing (clauses, translations, risks).", SearchIn, search_knowledge,
                 lambda i: f"Searching the knowledge base for “{i.get('query', '')}”"),
        ToolSpec("filter_contracts", "List contracts matching structured filters (country, type, urgency, "
                 "non-compete, recommended action).", FilterIn, filter_contracts, lambda i: "Filtering contracts"),
        ToolSpec("get_contract", "Everything known about one contract: terms, clauses, risks, documents, decision "
                 "and drafts.", ContractIn, get_contract, lambda i: f"Opening {i.get('contract_id', '')}"),
        ToolSpec("list_deadlines", "Action deadlines in date order (missed ones included).", DeadlinesIn,
                 list_deadlines, lambda i: f"Listing deadlines in the next {i.get('within_days', 90)} days"),
    ]


async def ask(ctx: RunContext, question: str, conversation_id: str) -> str:
    from . import simulator

    history = db.query("SELECT role, content FROM chat_messages WHERE conversation_id=? ORDER BY id",
                       (conversation_id,))
    db.insert("chat_messages", {"conversation_id": conversation_id, "role": "user", "content": question,
                                "run_id": ctx.run_id, "created_at": db.now_iso()})
    if ctx.mode == "live":
        s = ctx.settings
        answer = await run_agent(
            ctx, system=KNOWLEDGE_SYSTEM.format(company=s.company_name),
            history=[{"role": h["role"], "content": h["content"]} for h in history],
            user=f"(Today is {today().isoformat()}.)\n\n{question}", tools=knowledge_tools(), max_turns=12,
            max_tokens=16000, effort="low", stream_text=True)
    else:
        answer = await simulator.answer(ctx, knowledge_tools(), question)
    answer = answer or "I couldn't find an answer in the knowledge base."
    db.insert("chat_messages", {"conversation_id": conversation_id, "role": "assistant", "content": answer,
                                "run_id": ctx.run_id, "created_at": db.now_iso()})
    ctx.emit("answer.completed", conversation_id=conversation_id, text=answer)
    return answer[:200]
