"""Parsing a pulled email with a PDF attachment, without touching Gmail."""

from email.message import EmailMessage

import pytest

from connectors import gmail
from ledgerdb import db


def make_pdf(path, lines):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def test_save_message_extracts_pdf_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail, "RAW_DIR", tmp_path / "raw")
    pdf = tmp_path / "Q-778.pdf"
    make_pdf(pdf, ["QUOTATION Q-778", "Galvanized shackle 1/2in  qty 200  @ 4.10", "Total 820.00"])

    msg = EmailMessage()
    msg["From"] = "Pier 39 Boatworks <buyer@example.com>"
    msg["To"] = "desk@example.com"
    msg["Subject"] = "RE: quote for shackles"
    msg.set_content("Hi, confirming 200 shackles at $4.00 each as discussed. PO to follow.\n")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="Q-778.pdf")

    out = gmail.save_message("abc123", msg.as_bytes(), {"threadId": "t1", "labelIds": ["L1"]})
    assert out["subject"] == "RE: quote for shackles"
    assert "4.00 each" in out["body_excerpt"]
    att = out["attachments"][0]
    assert att["name"] == "Q-778.pdf" and att["pages"] == 1 and not att["no_text_layer"]
    from pathlib import Path
    assert "4.10" in Path(att["text_path"]).read_text(encoding="utf-8")


def test_mail_flows_to_the_right_queue(tmp_path):
    conn = db.connect(tmp_path / "l.db")
    db.init(conn)
    ctx = {"actor": "intake-1", "role": "intake"}
    db.append(conn, kind="mail.received", entity="mail:M-1", payload={"subject": "quote"}, **ctx)
    db.append(conn, kind="mail.received", entity="mail:M-2", payload={"subject": "invoice 77"}, **ctx)
    q = lambda name: sorted(r["entity"] for r in db.rows(conn, "SELECT * FROM open_items WHERE queue = ?", [name]))
    assert q("intake") == ["mail:M-1", "mail:M-2"]
    db.append(conn, kind="mail.classified", entity="mail:M-1", payload={"class": "customer_quote"}, **ctx)
    db.append(conn, kind="mail.classified", entity="mail:M-2", payload={"class": "vendor_bill"}, **ctx)
    assert q("intake") == [] and q("billing") == ["mail:M-2"]
    db.append(conn, kind="bill.received", entity="bill:B-1", actor="billing-1", role="billing",
              links=[{"rel": "from_mail", "to": "mail:M-2"}])
    assert q("billing") == ["bill:B-1"]  # now it needs its 3-way match instead
