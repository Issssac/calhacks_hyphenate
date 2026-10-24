import json
import sqlite3

import pytest

from ledgerdb import db
from ledgerdb.cli import main as cli_main


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "ledger.db")
    db.init(c)
    return c


def add(conn, **kw):
    return db.append(conn, **kw)


def quote_to_posted_so(conn):
    add(conn, actor="intake-1", role="intake", kind="quote.received", entity="quote:Q-1",
        sources=["gmail:abc"])
    add(conn, actor="intake-1", role="intake", kind="quote.extracted", entity="quote:Q-1",
        payload={"total": 100})
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001",
        payload={"total": 100}, links=[{"rel": "from_quote", "to": "quote:Q-1"}])
    add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="so:SO-1001",
        on_behalf_of="human:andy")
    add(conn, actor="orders-1", role="orders", kind="qbo.posted", entity="so:SO-1001",
        payload={"qbo_type": "Estimate", "qbo_id": "42"})


def test_append_and_chain(conn):
    r1 = add(conn, actor="intake-1", role="intake", kind="quote.received", entity="quote:Q-1")
    r2 = add(conn, actor="intake-1", role="intake", kind="quote.extracted", entity="quote:Q-1")
    assert r2["seq"] == r1["seq"] + 1
    row = conn.execute("SELECT prev_hash FROM events WHERE seq = ?", (r2["seq"],)).fetchone()
    assert row[0] == r1["hash"]
    assert db.verify(conn)["ok"]


def test_update_and_delete_refused(conn):
    add(conn, actor="intake-1", role="intake", kind="quote.received", entity="quote:Q-1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET actor = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events")


def test_verify_detects_tamper(conn):
    add(conn, actor="intake-1", role="intake", kind="quote.received", entity="quote:Q-1")
    add(conn, actor="intake-1", role="intake", kind="quote.extracted", entity="quote:Q-1",
        payload={"total": 100})
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute("UPDATE events SET payload = '{\"total\":1}' WHERE seq = 2")
    result = db.verify(conn)
    assert not result["ok"]
    assert result["problems"][0]["seq"] == 2


def test_role_cannot_write_other_kinds(conn):
    with pytest.raises(db.LedgerError, match="may not write"):
        add(conn, actor="intake-1", role="intake", kind="qbo.posted", entity="so:SO-1")
    with pytest.raises(db.LedgerError, match="may not write"):
        add(conn, actor="intake-1", role="intake", kind="approval.granted", entity="quote:Q-1")


def test_controller_needs_on_behalf_of(conn):
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001")
    with pytest.raises(db.LedgerError, match="on-behalf-of"):
        add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="so:SO-1001")


def test_no_self_approval(conn):
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001")
    add(conn, actor="human:andy", role="orders", kind="so.amended", entity="so:SO-1001")
    with pytest.raises(db.LedgerError, match="may not also approve"):
        add(conn, actor="orders-1", role="controller", kind="approval.granted",
            entity="so:SO-1001", on_behalf_of="human:andy")
    # the recording agent drafted it too, but the approver is a different person
    add(conn, actor="orders-1", role="controller", kind="approval.granted",
        entity="so:SO-1001", on_behalf_of="human:sam")


def test_posting_needs_approval_after_latest_draft(conn):
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001")
    with pytest.raises(db.LedgerError, match="no approval"):
        add(conn, actor="orders-1", role="orders", kind="qbo.posted", entity="so:SO-1001")
    add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="so:SO-1001",
        on_behalf_of="human:andy")
    add(conn, actor="orders-1", role="orders", kind="so.amended", entity="so:SO-1001")
    with pytest.raises(db.LedgerError, match="no approval"):
        add(conn, actor="orders-1", role="orders", kind="qbo.posted", entity="so:SO-1001")


def test_rejection_blocks_posting(conn):
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001")
    add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="so:SO-1001",
        on_behalf_of="human:andy")
    add(conn, actor="orders-1", role="controller", kind="approval.rejected", entity="so:SO-1001",
        on_behalf_of="human:andy")
    assert not db.is_approved(conn, "so:SO-1001")


def test_invoice_needs_approved_parent(conn):
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001")
    add(conn, actor="billing-1", role="billing", kind="invoice.drafted", entity="invoice:INV-1",
        links=[{"rel": "from_so", "to": "so:SO-1001"}])
    with pytest.raises(db.LedgerError, match="not approved"):
        add(conn, actor="billing-1", role="billing", kind="qbo.posted", entity="invoice:INV-1")


def test_bill_needs_match_or_approval(conn):
    add(conn, actor="orders-1", role="orders", kind="po.drafted", entity="po:PO-1")
    add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="po:PO-1",
        on_behalf_of="human:andy")
    add(conn, actor="orders-1", role="orders", kind="qbo.posted", entity="po:PO-1")
    add(conn, actor="billing-1", role="billing", kind="bill.received", entity="bill:B-1",
        payload={"total": 50}, links=[{"rel": "from_po", "to": "po:PO-1"}])
    with pytest.raises(db.LedgerError, match="no 3-way match"):
        add(conn, actor="billing-1", role="billing", kind="qbo.posted", entity="bill:B-1")
    add(conn, actor="billing-1", role="billing", kind="bill.matched", entity="bill:B-1")
    add(conn, actor="billing-1", role="billing", kind="qbo.posted", entity="bill:B-1")


def test_unbalanced_journal_refused(conn):
    with pytest.raises(db.LedgerError, match="does not balance"):
        add(conn, actor="billing-1", role="billing", kind="je.posted", entity="je:JE-1",
            payload={"lines": [{"account": "AR", "debit": 100}, {"account": "Revenue", "credit": 90}]})
    add(conn, actor="billing-1", role="billing", kind="je.posted", entity="je:JE-1",
        payload={"lines": [{"account": "AR", "debit": 100}, {"account": "Revenue", "credit": 100}]})
    tb = {r["account"]: r["balance"] for r in db.rows(conn, "SELECT * FROM trial_balance")}
    assert tb == {"AR": 100, "Revenue": -100}


def test_queues_follow_the_flow(conn):
    add(conn, actor="intake-1", role="intake", kind="quote.received", entity="quote:Q-1")
    q = lambda name: [r["entity"] for r in db.rows(conn, "SELECT * FROM open_items WHERE queue = ?", [name])]
    assert q("intake") == ["quote:Q-1"]
    add(conn, actor="intake-1", role="intake", kind="quote.extracted", entity="quote:Q-1",
        payload={"total": 100})
    assert q("intake") == [] and q("orders") == ["quote:Q-1"]
    add(conn, actor="orders-1", role="orders", kind="so.drafted", entity="so:SO-1001",
        payload={"total": 100}, links=[{"rel": "from_quote", "to": "quote:Q-1"}])
    assert q("orders") == []
    add(conn, actor="orders-1", role="controller", kind="approval.granted", entity="so:SO-1001",
        on_behalf_of="human:andy")
    assert q("orders") == ["so:SO-1001"]
    add(conn, actor="orders-1", role="orders", kind="qbo.posted", entity="so:SO-1001")
    assert q("orders") == [] and q("billing") == ["so:SO-1001"]
    add(conn, actor="billing-1", role="billing", kind="invoice.posted", entity="invoice:INV-1",
        payload={"total": 100}, links=[{"rel": "from_so", "to": "so:SO-1001"}])
    add(conn, actor="billing-1", role="billing", kind="qbo.posted", entity="invoice:INV-1")
    assert q("billing") == [] and q("cash") == ["invoice:INV-1"]
    add(conn, actor="cash-1", role="cash", kind="payment.applied", entity="payment:P-1",
        links=[{"rel": "applies_to", "to": "invoice:INV-1", "amount": 60}])
    assert q("cash") == ["invoice:INV-1"]  # short pay keeps it open
    add(conn, actor="cash-1", role="cash", kind="payment.applied", entity="payment:P-2",
        links=[{"rel": "applies_to", "to": "invoice:INV-1", "amount": 40}])
    assert q("cash") == []


def test_trace_walks_links(conn):
    quote_to_posted_so(conn)
    t = db.trace(conn, "so:SO-1001")
    assert t["entities"] == ["quote:Q-1", "so:SO-1001"]
    assert t["events"][0]["sources"] == ["gmail:abc"]


def test_feedback_open_until_addressed(conn):
    add(conn, actor="orders-1", role="orders", kind="feedback.recorded", entity="feedback:F-1",
        payload={"driver": "orders", "note": "use contract price"})
    assert len(db.rows(conn, "SELECT * FROM feedback_open")) == 1
    add(conn, actor="orders-1", role="orders", kind="workflow.adopted", entity="workflow:orders",
        payload={"version": 2}, links=[{"rel": "addresses", "to": "feedback:F-1"}])
    assert db.rows(conn, "SELECT * FROM feedback_open") == []
    assert db.rows(conn, "SELECT version FROM workflow_current")[0]["version"] == 2


def test_simulator_rows_are_labelled(conn):
    add(conn, actor="seed", role="simulator", kind="sim.payment", entity="payment:P-9")
    assert db.rows(conn, "SELECT payload FROM events")[0]["payload"]["simulated"] is True


def test_cli_roundtrip(tmp_path, capsys):
    path = str(tmp_path / "cli.db")
    assert cli_main(["--db", path, "init"]) == 0
    assert cli_main(["--db", path, "append", "--actor", "intake-1", "--role", "intake",
                     "--kind", "quote.received", "--entity", "quote:Q-1",
                     "--payload", '{"subject":"Quote"}', "--source", "gmail:abc"]) == 0
    capsys.readouterr()
    assert cli_main(["--db", path, "append", "--actor", "intake-1", "--role", "intake",
                     "--kind", "qbo.posted", "--entity", "so:SO-1"]) == 2
    assert "may not write" in json.loads(capsys.readouterr().out)["error"]
    assert cli_main(["--db", path, "queue", "intake"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["entity"] == "quote:Q-1"
    assert cli_main(["--db", path, "verify"]) == 0
