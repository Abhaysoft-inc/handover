"""Outbound email. Sandbox (default) never leaves the machine: messages are written
as .eml files and shown in the in-app Outbox. SMTP delivers for real, optionally
redirecting every message to a single safe address for demos."""

from __future__ import annotations

import asyncio
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..config import DATA_DIR, get_settings

OUTBOX = DATA_DIR / "outbox"


def _build(sender: str, to: list[str], cc: list[str], subject: str, body: str,
           attachments: list[tuple[str, bytes, str]]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="handover.local")
    msg.set_content(body)
    for filename, data, mime in attachments:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    return msg


async def send_mail(to: list[str], cc: list[str], subject: str, body: str,
                    attachments: list[tuple[str, bytes, str]] | None = None) -> dict:
    s = get_settings()
    attachments = attachments or []
    sender = s.smtp_from or s.counsel_email
    if s.mail_transport == "smtp" and s.smtp_host:
        real_to, real_cc = to, cc
        if s.mail_redirect_all_to:
            body = f"[Handover demo redirect — originally to: {', '.join(to + cc)}]\n\n{body}"
            real_to, real_cc = [s.mail_redirect_all_to], []
        msg = _build(sender, real_to, real_cc, subject, body, attachments)

        def deliver():
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(msg)

        await asyncio.to_thread(deliver)
        return {"status": "sent", "transport": "smtp",
                "detail": f"Delivered via {s.smtp_host}" + (f" (redirected to {s.mail_redirect_all_to})"
                                                             if s.mail_redirect_all_to else "")}
    msg = _build(sender, to, cc, subject, body, attachments)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", subject)[:60].strip("-") or "message"
    path = OUTBOX / f"{msg['Message-ID'].strip('<>').split('@')[0]}-{slug}.eml"
    path.write_bytes(bytes(msg))
    return {"status": "sandboxed", "transport": "sandbox", "detail": f"Saved to outbox ({path.name})"}
