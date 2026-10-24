"""End to end against a real local Odoo (database `revenue_test`). Skipped if Odoo isn't running.

quote -> SO + PO -> controller approval -> confirmed in Odoo -> goods received ->
invoice + vendor bill (3-way match) -> short customer payment -> still open.
"""

import os
import uuid

import pytest
import requests

from connectors import odoo
from ledgerdb import db

os.environ.setdefault("ODOO_DB", "revenue_test")


def _odoo_up() -> bool:
    try:
        return requests.get("http://localhost:8069/web/login", timeout=3).status_code < 500
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(not _odoo_up(), reason="local Odoo is not running")

ORDERS = {"actor": "orders-1", "role": "orders", "run_id": None, "workflow": "orders", "workflow_version": 1}
BILLING = {**ORDERS, "actor": "billing-1", "role": "billing", "workflow": "billing"}
CASH = {**ORDERS, "actor": "cash-1", "role": "cash", "workflow": "cash"}
SIM = {**ORDERS, "actor": "sim", "role": "simulator", "workflow": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_DB", "revenue_test")
    conn = db.connect(tmp_path / "l.db")
    db.init(conn)
    return conn, odoo.Odoo()


def approve(conn, entity):
    db.append(conn, actor="orders-1", role="controller", kind="approval.granted", entity=entity,
              on_behalf_of="human:test-controller")


def test_order_to_cash(env):
    conn, o = env
    tag = uuid.uuid4().hex[:6]
    item = f"Bracket-{tag}"
    so_payload = {"customer": f"Acme {tag}", "lines": [{"item": item, "qty": 10, "unit_price": 50, "taxes": []}],
                  "total": 500}
    po_payload = {"vendor": f"Parts {tag}", "lines": [{"item": item, "qty": 10, "unit_cost": 30, "taxes": []}],
                  "total": 300}

    db.append(conn, kind="so.drafted", entity="so:SO-1001", payload=so_payload, **ORDERS)
    db.append(conn, kind="po.drafted", entity="po:PO-1001", payload=po_payload,
              links=[{"rel": "for_so", "to": "so:SO-1001"}], **ORDERS)
    staged = odoo.stage(conn, o, "so:SO-1001", ORDERS)["odoo"]
    assert staged["state"] == "draft" and staged["amount_untaxed"] == 500
    odoo.stage(conn, o, "po:PO-1001", ORDERS)

    with pytest.raises(db.LedgerError, match="no approval"):
        odoo.post(conn, o, "so:SO-1001", ORDERS)
    assert o.read("sale.order", [staged["id"]], ["state"])[0]["state"] == "draft"

    approve(conn, "so:SO-1001")
    approve(conn, "po:PO-1001")
    assert odoo.post(conn, o, "so:SO-1001", ORDERS)["odoo"]["state"] == "sale"
    assert odoo.post(conn, o, "po:PO-1001", ORDERS)["odoo"]["state"] == "purchase"

    # invoice the customer
    db.append(conn, kind="invoice.drafted", entity="invoice:INV-1001", payload={"total": 500},
              links=[{"rel": "from_so", "to": "so:SO-1001"}], **BILLING)
    inv = odoo.stage(conn, o, "invoice:INV-1001", BILLING)["odoo"]
    assert inv["state"] == "draft" and inv["amount_total"] == 500
    posted = odoo.post(conn, o, "invoice:INV-1001", BILLING)
    assert posted["odoo"]["state"] == "posted" and posted["journal"]

    # goods arrive; vendor bills 32/unit instead of the agreed 30
    odoo.simulate_receipt(conn, o, "po:PO-1001", SIM)
    db.append(conn, kind="bill.received", entity="bill:B-1001",
              payload={"vendor": po_payload["vendor"], "vendor_ref": f"V-{tag}",
                       "lines": [{"item": item, "qty": 10, "unit_cost": 32}], "total": 320},
              links=[{"rel": "from_po", "to": "po:PO-1001"}], **BILLING)
    out = odoo.stage(conn, o, "bill:B-1001", BILLING)
    line = out["three_way"]["lines"][0]
    assert line["received"] == 10 and line["price_diff"] == 2
    with pytest.raises(db.LedgerError, match="3-way"):
        odoo.post(conn, o, "bill:B-1001", BILLING)

    # customer short-pays 450 of 500
    db.append(conn, kind="payment.received", entity="payment:P-1",
              payload={"customer": so_payload["customer"], "total": 450, "reference": f"ACH {tag}"}, **CASH)
    res = odoo.register_payment(conn, o, "payment:P-1", [("invoice:INV-1001", 450.0)], CASH, reason="short pay")
    assert res["invoices"][0]["payment_state"] == "partial"
    assert res["invoices"][0]["amount_residual"] == 50
    open_cash = db.rows(conn, "SELECT entity FROM open_items WHERE queue = 'cash'")
    assert {"entity": "invoice:INV-1001"} in open_cash

    for row in db.rows(conn, "SELECT entity, SUM(debit) d, SUM(credit) c FROM journal GROUP BY entity"):
        assert round(row["d"], 2) == round(row["c"], 2)
    assert db.verify(conn)["ok"]
