"""Build realistic .eml messages (with PDF attachments) for the simulated world."""

from __future__ import annotations

import email.utils
import io
from email.message import EmailMessage

from . import world


def pdf_bytes(title: str, lines: list[tuple], notes: list[str] | None = None, money_cols: str = "Unit price",
              party: str = "") -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, y, title)
    y -= 18
    c.setFont("Helvetica", 10)
    if party:
        c.drawString(60, y, party)
        y -= 26
    c.setFont("Helvetica-Bold", 10)
    for x, h in ((60, "Item"), (330, "Qty"), (390, money_cols), (480, "Amount")):
        c.drawString(x, y, h)
    y -= 16
    c.setFont("Helvetica", 10)
    total = 0.0
    for item, qty, price in lines:
        amount = round(qty * price, 2)
        total += amount
        c.drawString(60, y, str(item))
        c.drawRightString(360, y, f"{qty:g}")
        c.drawRightString(450, y, f"${price:,.2f}")
        c.drawRightString(540, y, f"${amount:,.2f}")
        y -= 15
    y -= 8
    c.setFont("Helvetica-Bold", 10)
    c.drawString(390, y, "Total")
    c.drawRightString(540, y, f"${total:,.2f}")
    y -= 28
    c.setFont("Helvetica", 9)
    for n in notes or []:
        c.drawString(60, y, n)
        y -= 13
    c.save()
    return buf.getvalue()


def message(sender: str, to: str, subject: str, body: str, attachments: list[tuple[str, bytes]] | None = None,
            message_id: str | None = None, in_reply_to: str | None = None, date: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = date or email.utils.formatdate(localtime=True)
    msg["Message-ID"] = message_id or email.utils.make_msgid(domain=world.MSGID_DOMAIN)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    for name, data in attachments or []:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return msg.as_bytes()
