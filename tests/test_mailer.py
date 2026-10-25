"""Outbound mail rules and finance's emailed decisions."""

import email
import email.policy
import json
from pathlib import Path

import pytest

from connectors import mailer
from desk import finance_reply
from ledgerdb import db

FIN = "finance@redwoodmicro.example"
CUST = "Halcyon Aerospace"
CUST_EMAIL = "procurement@halcyonaero.example"


class FakeOdoo:
    def search_read(self, model, domain, fields, limit=None):
        assert model == "res.partner"
        return [{"email": CUST_EMAIL}] if ("name", "=", CUST) in domain else []


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("DESK_STRUCTURE", "structure/redwood")
    c = db.connect()
    db.init(c)
    return c


PRICING = {"actor": "pricing-agent", "role": "pricing", "workflow": "pricing"}
QUOTING = {"actor": "quoting-agent", "role": "quoting", "workflow": "pricing"}


def _recommend(conn, rfq="quote:Q-1001"):
    db.append(conn, kind="quote.extracted", entity=rfq, actor="intake-agent", role="intake",
              payload={"customer": CUST, "lines": [{"mpn": "BME280", "qty": 100}]})
    rec = {"rfq": rfq, "customer": CUST, "lines": [{"mpn": "BME280", "qty": 100, "schedule": [
        {"qty": 1, "unit_price": 5.0, "unit_cost": 3.2, "lead_days": 3},
        {"qty": 100, "unit_price": 3.4, "unit_cost": 2.37, "lead_days": 3}]}]}
    return db.append(conn, kind="pricing.recommended", entity=rfq, payload=rec, **PRICING)["seq"]


def test_pricing_report_goes_only_to_finance(conn):
    _recommend(conn)
    with pytest.raises(mailer.MailRefused, match="only to finance"):
        mailer.send(conn, purpose="pricing_report", to=CUST_EMAIL, subject="x", text="x", rfq="quote:Q-1001", ctx=PRICING)
    out = mailer.send(conn, purpose="pricing_report", to=FIN, subject="Report", text="body", rfq="quote:Q-1001",
                      ctx=PRICING)
    raw = (mailer.outbox() / out["where"].split(":", 1)[1]).read_bytes()
    assert email.message_from_bytes(raw)["Message-ID"] == out["message_id"]
    assert mailer.sent_for(conn, out["message_id"])["purpose"] == "pricing_report"


def test_customer_quote_needs_finance_approval_and_the_address_on_file(conn):
    seq = _recommend(conn)
    send = lambda to: mailer.send(conn, purpose="customer_quote", to=to, subject="Quote", text="x",
                                  rfq="quote:Q-1001", customer=CUST, ctx=QUOTING, odoo=FakeOdoo())
    with pytest.raises(mailer.MailRefused, match="no finance approval"):
        send(CUST_EMAIL)
    db.append(conn, kind="approval.granted", entity="quote:Q-1001", actor="finance-reply", role="finance",
              on_behalf_of=f"human:{FIN}", payload={"decision": "approve", "recommendation_seq": seq})
    with pytest.raises(mailer.MailRefused, match="not the email on file"):
        send("buyer@phish.example")
    assert send(CUST_EMAIL)["where"].startswith("outbox:")
    # A newer recommendation needs a new approval before anything else is quoted.
    _recommend(conn)
    with pytest.raises(mailer.MailRefused, match="no finance approval"):
        send(CUST_EMAIL)


@pytest.mark.parametrize("body, decision", [
    ("APPROVE\n\nPriya", "approve"),
    ("approve.\n", "approve"),
    ("REJECT: too thin on the reel tier\n", "reject"),
    ("APPROVE WITH CHANGES\nBME280 100: 3.55\nBME280 1K: 3.10\n\nThanks", "approve_with_changes"),
    ("Looks fine to me\n", None),
    ("REJECT\n", None),
    ("APPROVE WITH CHANGES\nmake it cheaper\n", None),
])
def test_parse(body, decision):
    assert finance_reply.parse(body)["decision"] == decision


def test_parse_ignores_the_quoted_report():
    body = "APPROVE\n\nOn Mon, Desk wrote:\n> REJECT: nothing\n"
    assert finance_reply.parse(body)["decision"] == "approve"
    p = finance_reply.parse("APPROVE WITH CHANGES\nBME280 1,000: 3.1\n")
    assert p["changes"] == [{"mpn": "BME280", "qty": 1000, "unit_price": 3.1}]


def _reply(conn, tmp_path, sender, body, in_reply_to, n=1):
    p = tmp_path / f"body{n}.txt"
    p.write_text(body, encoding="utf-8")
    db.append(conn, kind="mail.received", entity=f"mail:M-R{n}", actor="forwarder", role="intake",
              payload={"from": sender, "in_reply_to": in_reply_to, "body_path": str(p), "delivered_via": "drop folder",
                       "subject": "Re: report"})


def test_finance_reply_becomes_an_approval_with_the_changed_schedule(conn, tmp_path):
    seq = _recommend(conn)
    rep = mailer.send(conn, purpose="pricing_report", to=FIN, subject="Report", text="x", rfq="quote:Q-1001",
                      ctx=PRICING, payload_extra={"recommendation_seq": seq})
    _reply(conn, tmp_path, "Someone <ceo@redwoodmicro.example>", "APPROVE", rep["message_id"], 1)
    _reply(conn, tmp_path, f"Priya <{FIN}>", "APPROVE WITH CHANGES\nBME280 100: 3.55\n", rep["message_id"], 2)
    out = finance_reply.process(conn)
    assert out[0].get("unclear") and "not in policy.finance_approvers" in out[0]["unclear"]
    assert out[1]["decision"] == "approve_with_changes"
    appr = mailer.approved_price(conn, "quote:Q-1001")
    assert appr["actor"] == f"human:{FIN}"
    sched = appr["approved"][0]["schedule"]
    assert [r["unit_price"] for r in sched] == [5.0, 3.55]
    assert sched[1]["changed_by_finance"] and sched[1]["recommended"] == 3.4
    assert "asserted" in appr["identity"]
    assert finance_reply.process(conn) == []  # each reply is handled once
