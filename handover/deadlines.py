"""Deadline math shared by the dashboard, the agents and the reminder engine."""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import db
from .config import get_settings, today

URGENCY_ORDER = {"missed": 0, "critical": 1, "soon": 2, "upcoming": 3, "ok": 4, "none": 5}


def parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d[:10])
    except ValueError:
        return None


def urgency(deadline: str | None, as_of: date | None = None) -> tuple[str, int | None]:
    d = parse(deadline)
    if d is None:
        return "none", None
    days = (d - (as_of or today())).days
    if days < 0:
        return "missed", days
    if days <= 14:
        return "critical", days
    if days <= 45:
        return "soon", days
    if days <= 120:
        return "upcoming", days
    return "ok", days


def schedule_deadline_alerts(contract_id: str, run_id: str | None = None) -> int:
    """Counsel never misses a window: alert 14 and 3 days before each action
    deadline (immediately if that point already passed), plus a missed-deadline alert."""
    c = db.one("SELECT * FROM contracts WHERE id=?", (contract_id,))
    if not c:
        return 0
    s = get_settings()
    db.execute("DELETE FROM reminders WHERE contract_id=? AND kind='deadline' AND status='scheduled'", (contract_id,))
    d = parse(c["action_deadline"])
    if d is None:
        return 0
    t = today()
    plan: list[tuple[date, str]] = []
    if d < t:
        plan.append((t, f"Deadline missed: {c['employee_name']}"))
    else:
        for lead in (14, 3):
            due = max(t, d - timedelta(days=lead))
            label = f"{(d - due).days} days left: {c['employee_name']}"
            if all(p[0] != due for p in plan):
                plan.append((due, label))
    for due, subject in plan:
        db.insert("reminders", {
            "contract_id": contract_id, "draft_id": None, "due_date": due.isoformat(),
            "recipients_json": json.dumps([s.counsel_email]),
            "subject": f"[Handover] {subject}",
            "message": (f"{c['employee_name']} — {c['job_title'] or 'contract'} ({c['employer_entity'] or ''}).\n"
                        f"Action deadline: {d.isoformat()} — {c['action_deadline_reason'] or ''}\n"
                        f"Recommended: {c['recommended_action'] or 'review'} — {c['recommendation_rationale'] or ''}\n"
                        f"Open Handover to assign an action."),
            "kind": "deadline", "status": "scheduled", "created_by": "deadline-guard",
            "created_at": db.now_iso(), "run_id": run_id,
        })
    return len(plan)
