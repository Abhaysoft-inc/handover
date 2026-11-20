"""Reminder engine: every 30 seconds, send whatever is due. A manual sweep with a
future 'as of' date lets a demo fast-forward time."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from . import db
from .config import today
from .connectors.mail import send_mail
from .events import changed, notify

log = logging.getLogger("handover.scheduler")


async def sweep(as_of: date | None = None) -> list[dict]:
    as_of = as_of or today()
    due = db.query("SELECT * FROM reminders WHERE status='scheduled' AND due_date <= ? ORDER BY due_date, id",
                   (as_of.isoformat(),))
    fired = []
    for r in due:
        recipients = db.loads(r["recipients_json"], [])
        try:
            res = await send_mail(recipients, [], r["subject"], r["message"])
        except Exception as e:  # keep it scheduled; surface the failure
            log.warning("reminder %s failed: %s", r["id"], e)
            continue
        db.insert("emails", {"kind": "reminder", "contract_id": r["contract_id"], "draft_id": r["draft_id"],
                             "reminder_id": r["id"], "to_json": json.dumps(recipients), "cc_json": "[]",
                             "subject": r["subject"], "body": r["message"], "attachments_json": "[]",
                             "status": res["status"], "transport": res["transport"], "created_at": db.now_iso(),
                             "sent_at": db.now_iso()})
        db.update("reminders", r["id"], {"status": "sent", "sent_at": db.now_iso()})
        db.log_activity(r["contract_id"], "reminder_sent", f"Reminder sent to {', '.join(recipients)}: {r['subject']}")
        notify("reminder.fired", reminder_id=r["id"], contract_id=r["contract_id"], subject=r["subject"],
               recipients=recipients, due_date=r["due_date"], kind=r["kind"])
        fired.append({"id": r["id"], "subject": r["subject"], "due_date": r["due_date"]})
    if fired:
        changed("reminders", "outbox", "contracts", "overview")
    return fired


async def loop() -> None:
    while True:
        try:
            await sweep()
        except Exception:
            log.exception("reminder sweep failed")
        await asyncio.sleep(30)
