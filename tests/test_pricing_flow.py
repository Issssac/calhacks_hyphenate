"""One RFQ through the pricing workflow, step by step, with each state's code check, without an agent:
extract -> queue -> assess -> recommend -> report to finance -> finance replies -> quote the customer ->
customer PO -> orders queue, priced at the approved tier."""

import json

import pytest

import checks.ledger_checks as lc
from connectors import mailer
from desk import finance_reply
from ledgerdb import db
from pricing import model as M
from pricing import send as S
from seed import redwood_history
from seed.worlds import redwood as W

RFQ = "quote:Q-1001"
CUST, CUST_EMAIL, FIN = "Halcyon Aerospace", "procurement@halcyonaero.example", "finance@redwoodmicro.example"
PRICING = {"actor": "pricing-agent", "role": "pricing", "workflow": "pricing"}


class FakeOdoo:
    """Supplier prices as Odoo would return them (from the Redwood world), and the customer on file."""
    def search_read(self, model, domain, fields, limit=None):
        if model == "res.partner":
            return [{"email": CUST_EMAIL}]
        if model == "product.supplierinfo":
            mpn = domain[-1][2]
            p = next(x for x in W.PRODUCTS if x["mpn"] == mpn)
            return [{"partner_id": [i, o["vendor"]], "min_qty": q, "price": c, "delay": o["lead_days"]}
                    for i, o in enumerate(p["offers"]) for q, c in o["breaks"]]
        raise AssertionError(model)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("DESK_STRUCTURE", "structure/redwood")
    redwood_history.write(redwood_history.generate(n=1500, seed=5), M.history_path())
    if M.model_path().exists():
        M.model_path().unlink()
    c = db.connect()
    db.init(c)
    monkeypatch.setattr(lc, "_conn", lambda: c)
    return c


def _focus(conn, entity):
    db.append(conn, kind="note.focus", entity="focus:pricing", payload={"entity": entity}, **PRICING)


def test_an_rfq_from_request_to_order(conn, tmp_path):
    intake = {"actor": "intake-agent", "role": "intake", "workflow": "intake"}
    db.append(conn, kind="mail.received", entity="mail:M-RFQ1", payload={"subject": "RFQ BME280",
              "message_id_header": "<rfq1@halcyonaero.example>", "from": CUST_EMAIL}, **intake)
    db.append(conn, kind="quote.received", entity=RFQ, payload={"customer": CUST},
              links=[{"rel": "from_mail", "to": "mail:M-RFQ1"}], **intake)
    db.append(conn, kind="quote.extracted", entity=RFQ, payload={"customer": CUST, "rfq": True, "total": None,
              "lines": [{"mpn": "BME280", "item": "BME280", "qty": 400, "source": "gmail:x#body"}]}, **intake)
    # Not an order yet: it waits for pricing.
    assert not any(r["entity"] == RFQ for r in lc._queue(conn, "orders"))
    assert {"entity": RFQ, "reason": "needs pricing"} in [{k: r[k] for k in ("entity", "reason")}
                                                          for r in lc._queue(conn, "pricing")]
    _focus(conn, RFQ)
    assert lc.pricing_focus_is_rfq()
    assert not lc.pricing_assessed()
    calls = {k: {"value": v, "evidence": [{"source": "gmail:x#body", "says": "..."}]}
             for k, v in {"growth": "growing", "wtp": "high", "urgency": "normal"}.items()}
    db.append(conn, kind="pricing.assessment", entity=RFQ, payload={"calls": calls}, **PRICING)
    assert not lc.pricing_assessed()  # made without reading the current rules
    from workflows.build import instructions_hash
    db.append(conn, kind="pricing.assessment", entity=RFQ,
              payload={"calls": calls, "instructions": instructions_hash("pricing", "assess")}, **PRICING)
    assert lc.pricing_assessed()
    rec = M.recommend(conn, RFQ, PRICING, odoo=FakeOdoo())
    assert lc.pricing_recommended()
    tier = next(r for r in rec["lines"][0]["schedule"] if r["qty"] == 400)
    assert tier["margin"] >= 0.15 - 1e-9
    S.send_report(conn, RFQ, PRICING)
    assert lc.pricing_report_sent()
    # Finance changes the 400 tier by reply.
    rep = db.rows(conn, "SELECT payload FROM events WHERE kind = 'mail.sent'")[0]["payload"]
    body = tmp_path / "reply.txt"
    body.write_text(f"APPROVE WITH CHANGES\nBME280 400: {tier['unit_price'] + 0.25:.2f}\n\nPriya", encoding="utf-8")
    db.append(conn, kind="mail.received", entity="mail:M-FIN1", payload={
        "from": f"Priya <{FIN}>", "in_reply_to": rep["message_id_header"], "body_path": str(body),
        "delivered_via": "drop folder"}, **intake)
    assert finance_reply.process(conn)[0]["decision"] == "approve_with_changes"
    assert getattr(lc, "pricing_decided_finance_review_approved")()
    assert lc.pricing_focus_is_approved()
    S.send_quote(conn, RFQ, {**PRICING, "role": "quoting"}, note="Ships in 3 days.", odoo=FakeOdoo())
    assert lc.pricing_quote_sent()
    assert not any(r["entity"] == RFQ for r in lc._queue(conn, "pricing"))
    # The customer's PO: now it is an order, at the price finance set.
    approved = round(tier["unit_price"] + 0.25, 2)
    assert lc.approved_unit_price(conn, RFQ, "BME280", 400) == approved
    assert lc.approved_unit_price(conn, RFQ, "BME280", 450) == approved   # between tiers: the tier below
    db.append(conn, kind="quote.accepted", entity=RFQ, payload={"customer": CUST, "customer_po": "HA-77",
              "lines": [{"mpn": "BME280", "item": "BME280", "qty": 400, "unit_price": approved, "source": "x"}],
              "total": round(400 * approved, 2)}, **intake)
    assert any(r["entity"] == RFQ and r["reason"] == "needs sales order" for r in lc._queue(conn, "orders"))


def test_the_cover_note_cannot_carry_a_price(conn):
    with pytest.raises(mailer.MailRefused, match="can't contain prices"):
        S.send_quote(conn, RFQ, PRICING, note="Special price $1.99 for you")
