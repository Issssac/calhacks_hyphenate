"""The QBO connector's posting rules, against an in-memory fake QBO."""

import itertools

import pytest

from connectors import qbo
from ledgerdb import db

MAPPING = {
    "income_account": {"value": "1", "name": "Sales"},
    "cogs_account": {"value": "2", "name": "COGS"},
    "ar_account": {"value": "3", "name": "AR"},
    "ap_account": {"value": "4", "name": "AP"},
    "bank_account": {"value": "5", "name": "Checking"},
    "undeposited_account": {"value": "6", "name": "Undeposited Funds"},
    "sales_tax_account": {"value": "7", "name": "Sales Tax Payable"},
}


class FakeQBO:
    def __init__(self):
        self.ids = itertools.count(100)
        self.objs = {}
        self.created = []

    def _new(self, kind, body):
        obj = {**body, "Id": str(next(self.ids)), "SyncToken": "0"}
        if "Line" in obj:
            obj["Line"] = [{**ln, "Id": str(i + 1)} for i, ln in enumerate(obj["Line"])]
            obj.setdefault("TotalAmt", round(sum(ln.get("Amount", 0) for ln in obj["Line"]), 2))
        self.objs[(kind, obj["Id"])] = obj
        self.created.append((kind, obj))
        return obj

    def create(self, kind, body):
        return self._new(kind, body)

    def get(self, kind, ident):
        return self.objs[(kind, ident)]

    def sparse_update(self, kind, obj, fields):
        obj = {**obj, **fields, "SyncToken": str(int(obj["SyncToken"]) + 1)}
        applied = sum(ln["Amount"] for ln in obj.get("Line", []))
        obj["UnappliedAmt"] = round(obj["TotalAmt"] - applied, 2)
        self.objs[(kind, obj["Id"])] = obj
        return obj

    def ensure_customer(self, name, email=None):
        return {"Id": "C-" + name}

    def ensure_vendor(self, name, email=None):
        return {"Id": "V-" + name}

    def ensure_item(self, name, mapping, service=False):
        return {"Id": "I-" + name}


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "l.db")
    db.init(conn)
    monkeypatch.setattr(qbo, "load_mapping", lambda q=None: MAPPING)
    return conn, FakeQBO()


ORDERS = {"actor": "orders-1", "role": "orders", "run_id": None, "workflow": "orders", "workflow_version": 1}
BILLING = {**ORDERS, "actor": "billing-1", "role": "billing", "workflow": "billing"}
CASH = {**ORDERS, "actor": "cash-1", "role": "cash", "workflow": "cash"}

SO = {"customer": "Acme", "lines": [{"item": "Widget", "qty": 10, "unit_price": 50, "amount": 500}], "total": 500}
PO = {"vendor": "Parts Co", "lines": [{"item": "Widget", "qty": 10, "unit_cost": 30, "amount": 300}], "total": 300}


def approve(conn, entity):
    db.append(conn, actor="orders-1", role="controller", kind="approval.granted", entity=entity,
              on_behalf_of="human:andy")


def test_unapproved_so_never_reaches_qbo(env):
    conn, fake = env
    db.append(conn, kind="so.drafted", entity="so:SO-1001", payload=SO, **ORDERS)
    with pytest.raises(db.LedgerError, match="no approval"):
        qbo.post(conn, fake, "so:SO-1001", ORDERS)
    assert fake.created == []


def test_full_flow_books_and_links(env):
    conn, fake = env
    db.append(conn, kind="so.drafted", entity="so:SO-1001", payload=SO, **ORDERS)
    db.append(conn, kind="po.drafted", entity="po:PO-1001", payload=PO,
              links=[{"rel": "for_so", "to": "so:SO-1001"}], **ORDERS)
    approve(conn, "so:SO-1001")
    approve(conn, "po:PO-1001")
    est = qbo.post(conn, fake, "so:SO-1001", ORDERS)["qbo"]
    po = qbo.post(conn, fake, "po:PO-1001", ORDERS)["qbo"]
    assert fake.created[0][0] == "Estimate" and fake.created[0][1]["TxnStatus"] == "Accepted"

    db.append(conn, kind="invoice.drafted", entity="invoice:INV-1001", payload={"total": 500},
              links=[{"rel": "from_so", "to": "so:SO-1001"}], **BILLING)
    out = qbo.post(conn, fake, "invoice:INV-1001", BILLING)
    inv_body = fake.created[-1][1]
    assert inv_body["LinkedTxn"] == [{"TxnId": est["qbo_id"], "TxnType": "Estimate"}]
    assert "journal" in out

    db.append(conn, kind="bill.received", entity="bill:B-1", payload={**PO, "vendor_ref": "PC-77"},
              links=[{"rel": "from_po", "to": "po:PO-1001"}], **BILLING)
    with pytest.raises(db.LedgerError, match="3-way"):
        qbo.post(conn, fake, "bill:B-1", BILLING)
    db.append(conn, kind="bill.matched", entity="bill:B-1", **BILLING)
    qbo.post(conn, fake, "bill:B-1", BILLING)
    bill_body = fake.created[-1][1]
    assert bill_body["Line"][0]["LinkedTxn"][0] == {"TxnId": po["qbo_id"], "TxnType": "PurchaseOrder",
                                                     "TxnLineId": "1"}

    tb = {r["account"]: r["balance"] for r in db.rows(conn, "SELECT * FROM trial_balance")}
    assert tb == {"AR": 500, "Sales": -500, "COGS": 300, "AP": -300}


def test_short_payment_stays_open(env):
    conn, fake = env
    db.append(conn, kind="so.drafted", entity="so:SO-1001", payload=SO, **ORDERS)
    approve(conn, "so:SO-1001")
    qbo.post(conn, fake, "so:SO-1001", ORDERS)
    db.append(conn, kind="invoice.drafted", entity="invoice:INV-1001", payload={"total": 500},
              links=[{"rel": "from_so", "to": "so:SO-1001"}], **BILLING)
    qbo.post(conn, fake, "invoice:INV-1001", BILLING)

    pay = fake._new("Payment", {"TotalAmt": 450.0, "CustomerRef": {"value": "C-Acme", "name": "Acme"}})
    qbo.record_payment(conn, fake, pay["Id"], CASH)
    qbo.apply_payment(conn, fake, f"payment:P-{pay['Id']}", [("invoice:INV-1001", 450.0)], CASH, "short pay")
    open_cash = db.rows(conn, "SELECT entity, reason FROM open_items WHERE queue = 'cash'")
    assert open_cash == [{"entity": "invoice:INV-1001", "reason": "open receivable"}]
    tb = {r["account"]: r["balance"] for r in db.rows(conn, "SELECT * FROM trial_balance")}
    assert tb["AR"] == 50 and tb["Undeposited Funds"] == 450

    with pytest.raises(qbo.QBOError, match="exceeds"):
        qbo.apply_payment(conn, fake, f"payment:P-{pay['Id']}", [("invoice:INV-1001", 500.0)], CASH)
