"""The simulated world: customers and suppliers reacting to what the desk does.

    python -m seed.sim quotes --phase 1     customers send their quotes (to the drop folder, or Gmail with --gmail)
    python -m seed.sim step                 goods arrive, suppliers bill, customers pay. Run it again whenever
                                            the desk has moved things on; it only does what is newly due.
    python -m seed.sim status

Nothing here touches the desk's decisions. It plays the outside world and labels everything
it does as simulated (role `simulator`, kinds `sim.*`), so the evidence table can separate
what the desk did from what the world did.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from connectors import increase, odoo
from connectors.env import DATA_DIR
from connectors.inbox import DROP
from ledgerdb import db
from . import mail, world
from .scenarios import BY_ID, SCENARIOS

STATE = DATA_DIR / "sim_state.json"
SIM = {"actor": "world-sim", "role": "simulator", "workflow": None, "run_id": None, "workflow_version": None}
DESK_ADDR = world.DESK_ADDR


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"sent": {}, "bills": {}, "paid": {}}


def save(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _deliver(raw: bytes, name: str, use_gmail: bool) -> str:
    if use_gmail:
        from connectors import gmail
        import email
        msg = email.message_from_bytes(raw)
        g = gmail.Gmail("send")
        atts = []
        tmp = DATA_DIR / "outbox"
        tmp.mkdir(parents=True, exist_ok=True)
        for part in msg.walk():
            if part.get_filename():
                p = tmp / part.get_filename()
                p.write_bytes(part.get_payload(decode=True))
                atts.append(p)
        body = next((p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_type() == "text/plain"), "")
        sender = msg["From"].split("<")[0].strip()
        return "gmail:" + gmail.send(g, "me", msg["Subject"], body, atts, sender_name=sender)["id"]
    DROP.mkdir(parents=True, exist_ok=True)
    (DROP / name).write_bytes(raw)
    return f"mailbox:{name}"


def send_quotes(conn, phase: int, use_gmail: bool = False) -> list[dict]:
    st = load()
    out = []
    for s in SCENARIOS:
        if s["phase"] != phase or s["id"] in st["sent"]:
            continue
        atts = []
        if s.get("pdf"):
            p = s["pdf"]
            atts.append((p["filename"], mail.pdf_bytes(p["title"], p["lines"], p.get("notes"), party=s["customer"])))
        if s.get("csv"):
            c = s["csv"]
            text = "MPN,Quantity\r\n" + "".join(f"{mpn},{qty}\r\n" for mpn, qty in c["lines"])
            atts.append((c["filename"], text.encode()))
        reply = st["sent"].get(s.get("reply_to") or "", {}).get("message_id")
        if reply is None and s.get("reply_to"):
            continue  # the message it answers hasn't gone out yet
        mid = f"<{s['id'].lower()}.{int(time.time())}@{world.MSGID_DOMAIN}>"
        body = s["body"]
        if "{need_by}" in body:
            need_by = (date.today() + timedelta(days=int(s.get("need_days", 21)))).isoformat()
            body = body.replace("{need_by}", need_by)
        raw = mail.message(s["sender"], DESK_ADDR, s["subject"], body, atts, message_id=mid, in_reply_to=reply)
        name = f"{s['id']}-{int(time.time())}.eml"
        where = _deliver(raw, name, use_gmail)
        st["sent"][s["id"]] = {"file": name, "message_id": mid, "where": where}
        db.append(conn, kind="sim.mail", entity=f"sim:{s['id']}", payload={"scenario": s["id"], "title": s["title"],
                                                                         "delivered": where}, **SIM)
        out.append({"scenario": s["id"], "title": s["title"], "delivered": where})
    save(st)
    return out


UPSTREAM = ("from_so", "for_so", "from_quote", "from_mail", "from_po")


def scenario_of(conn, entity: str, st: dict) -> str | None:
    """Walk back up the document chain (invoice -> SO -> quote -> mail) to the scenario's email."""
    by_file = {v["file"]: k for k, v in st["sent"].items()}
    seen, frontier, mails = {entity}, [entity], []
    while frontier:
        nxt = []
        for e in frontier:
            for rel in UPSTREAM:
                for parent in db.links_of(conn, e, rel):
                    if parent not in seen:
                        seen.add(parent)
                        nxt.append(parent)
                        if parent.startswith("mail:"):
                            mails.append(parent)
        frontier = nxt
    found = []
    for m in mails:
        for e in db.rows(conn, "SELECT kind, sources FROM events WHERE entity = ? AND kind = 'mail.received'", [m]):
            for src in e["sources"]:
                name = str(src).split("mailbox:", 1)[-1].split("#", 1)[0]
                if name in by_file:
                    found.append(by_file[name])
    # An amended deal links to the amendment's mail too; the deal belongs to the original.
    originals = [s for s in found if not BY_ID.get(s, {}).get("reply_to")]
    return (originals or found or [None])[0]


def _posted(conn, etype: str) -> list[tuple[str, dict]]:
    rows = db.rows(conn, "SELECT entity, payload FROM events WHERE kind = 'erp.posted' AND entity LIKE ? ORDER BY seq",
                   [etype + ":%"])
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["entity"]] = r["payload"]
    return list(latest.items())


def _quoted_tier(line: dict, qty: float) -> dict | None:
    tier = None
    for t in sorted(line.get("schedule", []), key=lambda r: r["qty"]):
        if qty >= t["qty"]:
            tier = t
    return tier


def answer_quotes(conn, st: dict) -> list[dict]:
    """Customers answer the quotes the desk sent: a PO for the lines they take, or a polite no. Each line is
    decided by the hidden demand model at the quoted price (seed/worlds/<world>_truth.py) against the
    scenario's fixed threshold u, so a run is repeatable."""
    import importlib
    truth = importlib.import_module(f"seed.worlds.{world.NAME}_truth")
    from pricing.features import is_repeat
    from pricing.model import history_path
    st.setdefault("answered", {})
    out = []
    for q in db.rows(conn, "SELECT seq, entity, payload FROM events WHERE kind = 'quote.sent' ORDER BY seq"):
        rfq, p = q["entity"], q["payload"]
        if rfq in st["answered"]:
            continue
        sid = scenario_of(conn, rfq, st)
        s = BY_ID.get(sid)
        if not s:
            continue
        tr, cust = s.get("truth", {}), s.get("world", {}).get("customer", {})
        rec = db.rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind = 'pricing.recommended' "
                            "ORDER BY seq DESC LIMIT 1", [rfq])
        costs = {(x["mpn"], r["qty"]): r.get("unit_cost") for x in (rec[0]["payload"]["lines"] if rec else [])
                 for r in x.get("schedule", [])}
        repeat = is_repeat(s["customer"], history_path())
        taken, considered = [], []
        for ln in p.get("lines", []):
            qty = float(cust.get("po_qty", {}).get(ln["mpn"], ln["qty"]))
            tier = _quoted_tier(ln, qty)
            cost = costs.get((ln["mpn"], tier["qty"])) if tier else None
            if not tier or not cost:
                continue
            pw = truth.p_win(tier["unit_price"] / cost, qty, tr.get("urgency", "normal"), tr.get("wtp", "normal"),
                             tr.get("growth", "flat"), repeat, s["customer"])
            considered.append({"mpn": ln["mpn"], "qty": qty, "price": tier["unit_price"], "p_win": round(pw, 3)})
            if pw >= float(cust.get("u", 0.5)):
                taken.append((ln["mpn"], int(qty), tier["unit_price"]))
        po = f"{''.join(w[0] for w in s['customer'].split())[:3].upper()}-PO-{4100 + len(st['answered'])}"
        if taken:
            subject = f"PO {po} for your quote"
            body = (f"Hello,\n\nPlease accept our purchase order {po} against your quote:\n\n"
                    + "".join(f"  {m}  qty {n:,}  at ${pr:,.4f} each\n" for m, n, pr in taken)
                    + "\nShip to our usual address. Thank you.\n")
            pdf = mail.pdf_bytes(f"PURCHASE ORDER {po}", [(m, n, pr) for m, n, pr in taken],
                                 [f"Supplier: {world.COMPANY['name']}", "Terms: Net 30"], party=s["customer"])
            atts = [(f"{po}.pdf", pdf)]
        else:
            subject = "Re: your quotation"
            body = "Hello,\n\nThank you for the quote. We've placed this order with another distributor.\n"
            atts = []
        mid = f"<{sid.lower()}-answer.{int(time.time())}@{world.MSGID_DOMAIN}>"
        raw = mail.message(s["sender"], DESK_ADDR, subject, body, atts, message_id=mid,
                           in_reply_to=p.get("message_id"))
        where = _deliver(raw, f"{sid}-answer-{int(time.time())}.eml", False)
        st["answered"][rfq] = {"scenario": sid, "po": po if taken else None, "taken": taken,
                               "considered": considered, "where": where}
        db.append(conn, kind="sim.customer_answer", entity=f"sim:{sid}-answer",
                  payload={"rfq": rfq, "scenario": sid, "accepted": bool(taken), "po": po if taken else None,
                           "lines": taken, "considered": considered, "delivered": where}, **SIM)
        out.append({"scenario": sid, "accepted": bool(taken), "lines": len(taken)})
    return out


def step(conn) -> dict:
    st = load()
    o = odoo.Odoo()
    done: dict = {"receipts": [], "bills": [], "payments": []}
    if world.FLOW == "rfq":
        from bench import finance
        done["finance"] = finance.respond(conn)
        done["customers"] = answer_quotes(conn, st)

    # 1. Suppliers ship: every confirmed PO gets its goods.
    for po, _ in _posted(conn, "po"):
        if conn.execute("SELECT 1 FROM events WHERE entity = ? AND kind = 'sim.receipt'", (po,)).fetchone():
            continue
        done["receipts"].append(odoo.simulate_receipt(conn, o, po, SIM)["received"])

    # 2. Suppliers bill: one invoice email per received PO, with any planted variance.
    for po, rec in _posted(conn, "po"):
        if po in st["bills"] or not conn.execute(
                "SELECT 1 FROM events WHERE entity = ? AND kind = 'sim.receipt'", (po,)).fetchone():
            continue
        pay = db.latest_payload(conn, po) or {}
        sid = scenario_of(conn, po, st)
        delta = (BY_ID.get(sid, {}).get("world", {}) or {}).get("bill_delta", {})
        vendor = next((v for v in world.VENDORS if v["name"] == pay.get("vendor")), None)
        lines = [(ln["item"], ln["qty"], round(float(ln["unit_cost"]) + delta.get(ln["item"], 0.0), 2))
                 for ln in pay.get("lines", [])]
        ref = f"{''.join(w[0] for w in pay.get('vendor', 'V').split())}-INV-{7000 + len(st['bills'])}"
        pdf = mail.pdf_bytes(f"INVOICE {ref}", lines, [f"Your PO: {rec.get('name')}", "Terms: Net 30",
                                                       "Remit by ACH to the account on file."],
                             money_cols="Unit cost", party=pay.get("vendor", ""))
        raw = mail.message(f"{pay.get('vendor')} Billing <{vendor['email'] if vendor else 'billing@vendor.example'}>",
                           DESK_ADDR, f"Invoice {ref} for PO {rec.get('name')}",
                           f"Please find attached invoice {ref} for your PO {rec.get('name')}.\n\nThank you.",
                           [(f"{ref}.pdf", pdf)])
        where = _deliver(raw, f"bill-{ref}.eml", False)
        st["bills"][po] = {"ref": ref, "where": where, "scenario": sid}
        db.append(conn, kind="sim.vendor_bill", entity=f"sim:{ref}", payload={"po": po, "lines": lines,
                                                                             "scenario": sid, "delivered": where}, **SIM)
        done["bills"].append(ref)

    # 3. Customers pay posted invoices, as each scenario says.
    b = increase.Increase()
    invoices = {inv: rec for inv, rec in _posted(conn, "invoice")}
    by_scn = {scenario_of(conn, inv, st): (inv, rec) for inv, rec in invoices.items()}
    for inv, rec in invoices.items():
        if inv in st["paid"]:
            continue
        sid = scenario_of(conn, inv, st)
        rule = (BY_ID.get(sid, {}).get("world", {}) or {}).get("pay")
        if not rule:
            continue
        amount = float(rec.get("amount_total", 0))
        group = [inv]
        if rule["kind"] == "bundled":
            other = by_scn.get(rule["with"])
            if not other:
                continue  # wait until the other invoice is posted too
            group.append(other[0])
            amount += float(other[1].get("amount_total", 0))
        elif rule["kind"] == "short":
            amount -= float(rule["less"])
        customer = BY_ID[sid]["customer"]
        names = ", ".join(invoices[g].get("name", g) for g in group)
        addenda = f"{rule.get('addenda', '')} {names}".strip()
        r = increase.simulate_inbound(conn, b, round(amount, 2), customer.upper(), "PAYMENT", SIM, addenda=addenda)
        for g in group:
            st["paid"][g] = {"scenario": sid, "amount": round(amount, 2), "transfer": r["inbound_ach_transfer"]}
        done["payments"].append({"invoices": group, "amount": round(amount, 2), "kind": rule["kind"]})
    save(st)
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="seed.sim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quotes")
    q.add_argument("--phase", type=int, default=1)
    q.add_argument("--gmail", action="store_true")
    q.add_argument("--if-revised", metavar="WORKFLOW",
                   help="send only once this workflow has adopted a revision (live version >= 2)")
    sub.add_parser("step")
    sub.add_parser("status")
    args = p.parse_args(argv)
    conn = db.connect()
    db.init(conn)
    if args.cmd == "quotes":
        # A revision learned in this run: adopted in answer to feedback (not carried over by a reset).
        v = conn.execute("SELECT 1 FROM events e WHERE e.kind = 'workflow.adopted' AND e.entity = ?"
                         " AND EXISTS (SELECT 1 FROM doc_links l WHERE l.seq = e.seq AND l.rel = 'addresses')"
                         " LIMIT 1", (f"workflow:{args.if_revised}",)).fetchone() if args.if_revised else None
        if args.if_revised and not v:
            out = {"waiting": f"{args.if_revised} has not adopted a revision yet"}
        else:
            out = send_quotes(conn, args.phase, args.gmail)
    elif args.cmd == "step":
        out = step(conn)
    else:
        out = load()
    sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
