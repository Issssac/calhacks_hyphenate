"""Outbound mail for the desk, with the rules on who may receive what checked in code.

    send(conn, purpose=..., to=..., subject=..., text=..., html=..., images=..., attachments=..., entity=..., ctx=...)

Two purposes, two rules:
  pricing_report   internal: our costs, margins and model output. It goes ONLY to an address in
                   policy.finance_approvers. A report can never be sent to a customer by mistake.
  customer_quote   external: the prices finance approved. It goes ONLY to the customer's email on file in Odoo, and
                   only once finance has approved the price (an approval.granted by role finance on the RFQ, after its
                   latest recommendation). An address taken from an email can't redirect it.

Transport: Gmail (the desk's `gmail-send` token) when MAIL_TRANSPORT=gmail, otherwise the outbox folder
(<desk home>/outbox/*.eml), which is how the simulated finance team and customers receive mail. Every send is
recorded as `mail.sent` (entity mail:S-<id>) with its Message-ID, so a reply can be matched to what it answers.
"""

from __future__ import annotations

import base64
import email.policy
import email.utils
import hashlib
import mimetypes
import os
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ledgerdb import db
from ledgerdb.policy import policy

PURPOSES = ("pricing_report", "customer_quote")


class MailRefused(RuntimeError):
    """A send the rules refused. The message says which rule."""


def _addr(s: str) -> str:
    return email.utils.parseaddr(s or "")[1].lower()


def outbox() -> Path:
    return db.desk_home() / "outbox"


def finance_approvers() -> list[str]:
    return [a.lower() for a in policy().get("finance_approvers", [])]


def approved_price(conn, rfq: str) -> dict | None:
    """Finance's approval of the RFQ's latest recommendation, or None if there isn't one yet."""
    rec = conn.execute("SELECT MAX(seq) FROM events WHERE entity = ? AND kind = 'pricing.recommended'",
                       (rfq,)).fetchone()[0]
    if rec is None:
        return None
    rows = db.rows(conn, "SELECT seq, actor, payload FROM events WHERE entity = ? AND kind = 'approval.granted' "
                         "AND role = 'finance' AND seq > ? ORDER BY seq DESC LIMIT 1", [rfq, rec])
    if not rows:
        return None
    later_reject = conn.execute("SELECT 1 FROM events WHERE entity = ? AND kind = 'approval.rejected' AND seq > ?",
                                (rfq, rows[0]["seq"])).fetchone()
    return None if later_reject else {**rows[0]["payload"], "seq": rows[0]["seq"], "actor": rows[0]["actor"]}


def customer_email_on_file(customer: str, odoo=None) -> str | None:
    if odoo is None:
        from connectors.odoo import Odoo
        odoo = Odoo()
    found = odoo.search_read("res.partner", [("name", "=", customer), ("customer_rank", ">", 0)], ["email"], limit=1)
    return (found[0]["email"] or "").lower() if found else None


def check(conn, purpose: str, to: str, rfq: str | None, customer: str | None = None, odoo=None) -> None:
    if purpose not in PURPOSES:
        raise MailRefused(f"unknown purpose {purpose!r}: use one of {', '.join(PURPOSES)}")
    dest = _addr(to)
    if purpose == "pricing_report":
        allowed = finance_approvers()
        if dest not in allowed:
            raise MailRefused(f"a pricing report goes only to finance ({', '.join(allowed) or 'none configured'}), "
                              f"not {dest}")
        return
    if not rfq:
        raise MailRefused("a customer quote must name the RFQ it answers")
    if approved_price(conn, rfq) is None:
        raise MailRefused(f"{rfq} has no finance approval after its latest recommendation; nothing can be quoted yet")
    on_file = customer_email_on_file(customer, odoo) if customer else None
    if not on_file:
        raise MailRefused(f"no email on file in Odoo for customer {customer!r}")
    if dest != on_file:
        raise MailRefused(f"{dest} is not the email on file for {customer} ({on_file}); quotes go only there")


def build(sender: str, to: str, subject: str, text: str, html: str | None = None,
          images: list[tuple[str, Path]] | None = None, attachments: list[tuple[str, bytes]] | None = None,
          in_reply_to: str | None = None, domain: str = "desk.example") -> tuple[bytes, str]:
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    mid = email.utils.make_msgid(domain=domain)
    msg["Message-ID"] = mid
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
        html_part = msg.get_payload()[-1]
        for cid, path in images or []:
            if path and Path(path).exists():
                html_part.add_related(Path(path).read_bytes(), maintype="image", subtype="png", cid=f"<{cid}>",
                                      filename=Path(path).name)
    for name, data in attachments or []:
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes(), mid


def send(conn, *, purpose: str, to: str, subject: str, text: str, ctx: dict, rfq: str | None = None,
         customer: str | None = None, html: str | None = None, images: list[tuple[str, Path]] | None = None,
         attachments: list[tuple[str, bytes]] | None = None, in_reply_to: str | None = None,
         links: list[tuple[str, str]] | None = None, payload_extra: dict | None = None, odoo=None) -> dict[str, Any]:
    from seed import world
    check(conn, purpose, to, rfq, customer, odoo)
    raw, mid = build(world.DESK_ADDR, to, subject, text, html, images, attachments, in_reply_to,
                     domain=world.MSGID_DOMAIN.replace("sim.", "", 1))
    digest = hashlib.sha256(raw).hexdigest()
    sid = f"S{digest[:16]}"
    transport = os.environ.get("MAIL_TRANSPORT", "outbox")
    where: str
    if transport == "gmail":
        from connectors import gmail
        g = gmail.Gmail("send")
        sent = g.request("POST", "/messages/send", json={"raw": base64.urlsafe_b64encode(raw).decode()})
        where = f"gmail:{sent['id']}"
    else:
        outbox().mkdir(parents=True, exist_ok=True)
        name = f"{sid}-{purpose}-{re.sub(r'[^A-Za-z0-9-]', '-', rfq or 'mail')}.eml"
        (outbox() / name).write_bytes(raw)
        where = f"outbox:{name}"
    payload = {"purpose": purpose, "to": to, "subject": subject, "message_id_header": mid, "in_reply_to": in_reply_to,
               "transport": transport, "where": where, "sha256": digest, "rfq": rfq, "customer": customer,
               "attachments": [n for n, _ in attachments or []], "images": [str(p) for _, p in images or []],
               **(payload_extra or {})}
    rel = [{"rel": r, "to": t} for r, t in links or []]
    if rfq:
        rel.append({"rel": "about", "to": rfq})
    ev = db.append(conn, kind="mail.sent", entity=f"mail:{sid}", payload=payload, sources=[where],
                   links=rel, **ctx)
    return {"entity": f"mail:{sid}", "seq": ev["seq"], "message_id": mid, "where": where}


def sent_for(conn, message_id: str) -> dict | None:
    """The desk's own outbound message with this Message-ID (what a reply answers), if any."""
    if not message_id:
        return None
    rows = db.rows(conn, "SELECT seq, entity, payload FROM events WHERE kind = 'mail.sent' "
                         "AND json_extract(payload, '$.message_id_header') = ?", [message_id.strip()])
    return {**rows[0]["payload"], "entity": rows[0]["entity"], "seq": rows[0]["seq"]} if rows else None
