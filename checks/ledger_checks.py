"""Predicates behind the `hard` transitions in every workflow.

A hard transition moves only when the agent says yes *and* the function here
returns True. They read the ledger, never the agent's word, and each one
records what it saw as a check.result event, so a judge can see why a
workflow did or didn't move.

WidgeX does not document what it passes a check, so every function accepts
anything and ignores it. The item being worked on comes from the ledger: an
agent declares it with `python -m ledgerdb focus <driver> <entity>`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledgerdb import db  # noqa: E402
from ledgerdb.policy import policy, price_tolerance, structure_dir  # noqa: E402

DRIVERS = ("intake", "orders", "billing", "cash", "reconcile", "pricing")
EPS = 0.005


def _conn():
    return db.connect(db.default_path())


def _record(conn, name: str, ok: bool, detail: dict) -> bool:
    try:
        db.append(conn, actor=f"check:{name}", role="checker", kind="check.result", entity=f"check:{name}",
                  payload={"ok": bool(ok), **detail})
    except Exception:
        pass  # recording must never be the reason a check fails
    return bool(ok)


def focus(conn, driver: str) -> str | None:
    row = conn.execute("SELECT json_extract(payload, '$.entity') FROM events WHERE entity = ? AND kind = 'note.focus'"
                       " ORDER BY seq DESC LIMIT 1", (f"focus:{driver}",)).fetchone()
    return row[0] if row else None


def _kinds(conn, entity: str) -> list[tuple[int, str, dict]]:
    return [(s, k, json.loads(p)) for s, k, p in conn.execute(
        "SELECT seq, kind, payload FROM events WHERE entity = ? ORDER BY seq", (entity,))]


def _last(conn, entity: str, *kinds: str) -> tuple[int, dict] | None:
    marks = ",".join("?" * len(kinds))
    row = conn.execute(f"SELECT seq, payload FROM events WHERE entity = ? AND kind IN ({marks})"
                       " ORDER BY seq DESC LIMIT 1", (entity, *kinds)).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def _last_def(conn, entity: str) -> int:
    return db._last_defining_seq(conn, entity) or 0


def _children(conn, parent: str, rel: str, prefix: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT src FROM doc_links WHERE rel = ? AND dst = ? AND src LIKE ? ORDER BY seq",
        (rel, parent, prefix + ":%"))]


def _queue(conn, name: str) -> list[dict]:
    return db.rows(conn, "SELECT entity, reason FROM open_items WHERE queue = ? ORDER BY since_seq", [name])


def _held(conn, payment: str) -> bool:
    last = _last(conn, payment, "payment.unmatched")
    return bool(last) and last[1].get("held") is True


def _actionable(conn, driver: str) -> list[dict]:
    rows = _queue(conn, driver)
    if driver == "billing":
        rows = [r for r in rows if r["reason"] != "awaiting vendor bill"]
    if driver == "cash":
        keep = []
        for r in rows:
            if r["reason"] == "open receivable":
                continue
            if r["reason"] == "open payable" and _children(conn, r["entity"], "pays", "billpayment"):
                continue  # a payment is already proposed for it
            if r["reason"] == "unapplied cash" and _held(conn, r["entity"]):
                continue  # the controller chose to hold it unapplied
            if r["reason"] == "bank receipt to match" and _last(conn, r["entity"], "recon.explained"):
                continue  # explained as not this run's customer money (e.g. the opening position)
            keep.append(r)
        rows = keep
    if driver == "reconcile":
        last_recon = conn.execute("SELECT MAX(seq) FROM events WHERE kind = 'recon.result'").fetchone()[0] or 0
        last_money = conn.execute("SELECT MAX(seq) FROM events WHERE kind IN ('je.posted', 'bank.transaction')"
                                  ).fetchone()[0] or 0
        rows = [{"entity": "recon:books", "reason": "new activity since last reconciliation"}] \
            if last_money > last_recon else []
    return rows


def _staged_after_def(conn, entity: str) -> bool:
    last = _last(conn, entity, "erp.staged")
    return bool(last) and last[0] > _last_def(conn, entity)


def _posted_after_def(conn, entity: str) -> bool:
    last = _last(conn, entity, "erp.posted")
    return bool(last) and last[0] > _last_def(conn, entity)


def _sum_lines(lines: list[dict], price_key: str) -> float:
    return round(sum(float(l.get("qty", 1)) * float(l[price_key]) for l in lines), 2)


# ---------------------------------------------------------------- generic per-driver

def _queue_empty(driver: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        rows = _actionable(conn, driver)
        return _record(conn, f"{driver}_queue_empty", not rows, {"open": rows[:10]})
    check.__name__ = f"{driver}_queue_empty"
    return check


def _focus_set(driver: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        f = focus(conn, driver)
        ok = bool(f) and any(r["entity"] == f for r in _actionable(conn, driver))
        return _record(conn, f"{driver}_focus_set", ok, {"focus": f})
    check.__name__ = f"{driver}_focus_set"
    return check


def _has_feedback(driver: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        rows = db.rows(conn, "SELECT entity, note FROM feedback_open WHERE driver = ?", [driver])
        return _record(conn, f"{driver}_has_feedback", bool(rows), {"feedback": rows[:5]})
    check.__name__ = f"{driver}_has_feedback"
    return check


def _feedback_closed(driver: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        rows = db.rows(conn, "SELECT entity FROM feedback_open WHERE driver = ?", [driver])
        return _record(conn, f"{driver}_feedback_closed", not rows, {"still_open": rows[:5]})
    check.__name__ = f"{driver}_feedback_closed"
    return check


def _proposal_valid(driver: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        ok, detail = proposal_ok(conn, driver)
        return _record(conn, f"{driver}_proposal_valid", ok, detail)
    check.__name__ = f"{driver}_proposal_valid"
    return check


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def proposal_ok(conn, driver: str) -> tuple[bool, dict]:
    path = ROOT / "workflows" / "proposed" / f"{driver}.json"
    if not path.exists():
        return False, {"problem": f"{path.relative_to(ROOT)} does not exist"}
    sha = file_sha(path)
    row = conn.execute("SELECT json_extract(payload, '$.ok') FROM events WHERE kind = 'workflow.validated'"
                       " AND entity = ? AND json_extract(payload, '$.sha256') = ? ORDER BY seq DESC LIMIT 1",
                       (f"workflow:{driver}", sha)).fetchone()
    if not row:
        return False, {"problem": "no workflow.validated for this exact file (edit after checking?)", "sha256": sha}
    return bool(row[0]), {"sha256": sha, "validated_ok": bool(row[0])}


# ---- controller decisions through the ledger (approval mode "ledger")

REVIEW_STATES = {"intake": ["clarify", "adopt"], "orders": ["controller_review", "adopt"],
                 "billing": ["invoice_review", "bill_review", "adopt"],
                 "cash": ["cash_review", "payment_review", "adopt"], "reconcile": ["report", "adopt"],
                 "pricing": ["finance_review", "adopt"]}


def latest_request(conn, driver: str, state: str | None = None) -> tuple[int, str, dict] | None:
    sql = "SELECT seq, entity, payload FROM events WHERE kind = 'approval.requested' AND workflow = ?"
    params: list = [driver]
    if state:
        sql += " AND json_extract(payload, '$.review_state') = ?"
        params.append(state)
    row = conn.execute(sql + " ORDER BY seq DESC LIMIT 1", params).fetchone()
    return (row[0], row[1], json.loads(row[2])) if row else None


def decision_after(conn, entity: str, seq: int) -> tuple[str, dict] | None:
    row = conn.execute("SELECT kind, payload FROM events WHERE entity = ? AND seq > ?"
                       " AND kind IN ('approval.granted', 'approval.rejected') ORDER BY seq DESC LIMIT 1",
                       (entity, seq)).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def _decision(driver: str, want: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        req = latest_request(conn, driver)
        dec = decision_after(conn, req[1], req[0]) if req else None
        ok = bool(dec) and dec[0] == want
        return _record(conn, check.__name__, ok, {"request": req and {"seq": req[0], "entity": req[1]},
                                                  "decision": dec and dec[0]})
    check.__name__ = f"{driver}_decision_{'approved' if want == 'approval.granted' else 'rejected'}"
    return check


def _awaiting(driver: str, state: str) -> Callable[..., bool]:
    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        req = latest_request(conn, driver, state)
        ok = bool(req) and decision_after(conn, req[1], req[0]) is None
        return _record(conn, check.__name__, ok, {"request": req and {"seq": req[0], "entity": req[1]}})
    check.__name__ = f"{driver}_awaiting_{state}"
    return check


def decision_unacted(conn, request: tuple[int, str, dict], workflow: str) -> tuple[str, dict] | None:
    """The decision on a request, if the requesting workflow hasn't acted on it since."""
    seq, entity, _ = request
    dec = conn.execute("SELECT seq, kind, payload FROM events WHERE entity = ? AND seq > ? AND kind IN"
                       " ('approval.granted', 'approval.rejected') ORDER BY seq DESC LIMIT 1", (entity, seq)).fetchone()
    if not dec:
        return None
    acted = conn.execute(
        "SELECT 1 FROM events WHERE seq > ? AND workflow = ? AND kind NOT LIKE 'approval.%'"
        " AND kind NOT IN ('check.result', 'note.focus', 'erp.changed', 'note.dispatch')"
        " AND (entity = ? OR seq IN (SELECT seq FROM doc_links WHERE dst = ?)) LIMIT 1",
        (dec[0], workflow, entity, entity)).fetchone()
    return None if acted else (dec[1], json.loads(dec[2]))


def _decided(driver: str, state: str, word: str) -> Callable[..., bool]:
    want = "approval.granted" if word == "approved" else "approval.rejected"

    def check(*_a: Any, **_k: Any) -> bool:
        conn = _conn()
        # Every item this workflow asked about at this review point (its latest request per item),
        # oldest first: a decision on an earlier item must not be buried by later requests.
        req, dec = None, None
        for seq, entity, payload in conn.execute(
                "SELECT MAX(seq), entity, payload FROM events WHERE kind = 'approval.requested' AND workflow = ?"
                " AND json_extract(payload, '$.review_state') = ? GROUP BY entity ORDER BY MAX(seq)", (driver, state)):
            d = decision_unacted(conn, (seq, entity, json.loads(payload)), driver)
            if d and d[0] == want:
                req, dec = (seq, entity, json.loads(payload)), d
                break
        ok = bool(dec)
        if ok:
            # Point the agent at the item: the transition is about to be taken.
            try:
                db.append(conn, actor=f"check:{check.__name__}", role="checker", kind="note.focus",
                          entity=f"focus:{driver}", payload={"entity": req[1], "via": "decision"}, workflow=driver)
            except Exception:
                pass
        return _record(conn, check.__name__, ok, {"request": req and {"seq": req[0], "entity": req[1]},
                                                  "decision": dec and dec[0]})
    check.__name__ = f"{driver}_decided_{state}_{word}"
    return check


def controller_nothing_open(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    # Finance answers its own review (by email), so a finance_review is never the controller's to answer.
    open_reqs = [ent for ent, seq, state in conn.execute(
        "SELECT entity, MAX(seq), json_extract(payload, '$.review_state') FROM events "
        "WHERE kind = 'approval.requested' GROUP BY entity")
        if state != "finance_review" and decision_after(conn, ent, seq) is None]
    return _record(conn, "controller_nothing_open", not open_reqs, {"open_requests": open_reqs[:10]})


for _d in DRIVERS:
    for _st in REVIEW_STATES[_d]:
        for _w in ("approved", "rejected"):
            _fn = _decided(_d, _st, _w)
            globals()[_fn.__name__] = _fn
    for _factory in (_queue_empty, _focus_set, _has_feedback, _feedback_closed, _proposal_valid):
        _fn = _factory(_d)
        globals()[_fn.__name__] = _fn
    for _want in ("approval.granted", "approval.rejected"):
        _fn = _decision(_d, _want)
        globals()[_fn.__name__] = _fn
    for _st in REVIEW_STATES[_d]:
        _fn = _awaiting(_d, _st)
        globals()[_fn.__name__] = _fn


# ---------------------------------------------------------------- intake

def _focus_quote(conn) -> str | None:
    f = focus(conn, "intake")
    if f and f.startswith("mail:"):
        q = _children(conn, f, "from_mail", "quote")
        return q[-1] if q else None
    return f


def intake_extracted(*_a: Any, **_k: Any) -> bool:
    """The focused quote has an extraction where every line cites where it came from."""
    conn = _conn()
    q = _focus_quote(conn)
    last = _last(conn, q, "quote.extracted", "quote.clarified", "quote.amended") if q else None
    accepted = _last(conn, q, "quote.accepted") if q else None
    if accepted and (not last or accepted[0] > last[0]):
        last = accepted  # the customer's PO accepting our quote: a priced order, checked like a quote
    problems = []
    if not last:
        problems.append("no quote.extracted, quote.clarified or quote.amended")
    else:
        p = last[1]
        rfq = bool(p.get("rfq")) and last is not accepted
        if not p.get("customer"):
            problems.append("no customer")
        lines = p.get("lines") or []
        if not lines:
            problems.append("no lines")
        for i, ln in enumerate(lines, 1):
            if not ln.get("source"):
                problems.append(f"line {i} cites no source")
            if rfq:
                # A request for quote asks us for prices: it needs the part and quantity, not a price.
                if not (ln.get("mpn") or ln.get("item")) or not ln.get("qty"):
                    problems.append(f"line {i} needs an mpn and a qty")
            elif "unit_price" not in ln:
                problems.append(f"line {i} has no unit_price")
        if not rfq and lines and p.get("total") is not None and \
                abs(_sum_lines(lines, "unit_price") - float(p["total"])) > 0.01 + EPS:
            problems.append(f"lines sum to {_sum_lines(lines, 'unit_price')} but total says {p['total']}")
    return _record(conn, "intake_extracted", not problems, {"quote": q, "problems": problems})


def intake_handled(*_a: Any, **_k: Any) -> bool:
    """The focused mail is classified, and if it was a quote, the quote is resolved."""
    conn = _conn()
    f = focus(conn, "intake")
    problems = []
    if not f:
        problems.append("no focus")
    else:
        mail = f if f.startswith("mail:") else None
        if mail and not _last(conn, mail, "mail.classified"):
            problems.append("mail not classified")
        quotes = _children(conn, mail, "from_mail", "quote") if mail else [f]
        for q in quotes:
            if not _last(conn, q, "quote.extracted", "quote.clarified", "quote.amended", "quote.duplicate",
                         "quote.rejected", "quote.accepted"):
                problems.append(f"{q} not resolved")
    return _record(conn, "intake_handled", not problems, {"focus": f, "problems": problems})


# ---------------------------------------------------------------- orders

def _orders_docs(conn) -> tuple[str | None, str | None, list[str]]:
    f = focus(conn, "orders")
    if not f:
        return None, None, []
    if f.startswith("quote:"):
        sos = _children(conn, f, "from_quote", "so")
        so = sos[-1] if sos else None
        quote = f
    else:
        so = f if f.startswith("so:") else None
        quote = (db.links_of(conn, so, "from_quote") or [None])[0] if so else None
    pos = _children(conn, so, "for_so", "po") if so else []
    return quote, so, pos


def orders_focus_is_approved(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "orders")
    ok = any(r["entity"] == f and r["reason"] == "approved, needs posting" for r in _queue(conn, "orders"))
    return _record(conn, "orders_focus_is_approved", ok, {"focus": f})


def orders_focus_is_quote(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "orders")
    ok = any(r["entity"] == f and r["reason"] in ("needs sales order", "quote amended after SO",
                                                  "needs approval request")
             for r in _queue(conn, "orders"))
    return _record(conn, "orders_focus_is_quote", ok, {"focus": f})


def orders_drafts_valid(*_a: Any, **_k: Any) -> bool:
    """SO and POs exist, tie out, keep the margin floor, never cost more than they sell, and are staged."""
    conn = _conn()
    pol = policy()
    quote, so, pos = _orders_docs(conn)
    problems: list[str] = []
    detail: dict[str, Any] = {"quote": quote, "so": so, "pos": pos}
    sop = db.latest_payload(conn, so) if so else None
    if not sop:
        problems.append("no sales order drafted for the focus")
    else:
        so_lines = sop.get("lines") or []
        so_total = _sum_lines(so_lines, "unit_price")
        if abs(so_total - float(sop.get("total", so_total))) > pol["total_tie_abs"] + EPS:
            problems.append(f"SO lines sum to {so_total}, SO total says {sop.get('total')}")
        qp = None
        rfq = False
        if quote:
            q = _last(conn, quote, "quote.clarified", "quote.extracted", "quote.amended")
            qp = q[1] if q else None
            rfq = bool(qp and qp.get("rfq"))
        if rfq:
            # An RFQ: the customer's PO (quote.accepted) sets the quantities, and every unit price must be the one
            # finance approved and the customer was quoted, at that quantity's tier.
            acc = _last(conn, quote, "quote.accepted")
            qp = acc[1] if acc else None
            for ln in so_lines:
                want = approved_unit_price(conn, quote, ln.get("mpn") or ln["item"], float(ln.get("qty", 1)))
                if want is None:
                    problems.append(f"{ln['item']} at {ln.get('qty')} was never quoted to the customer")
                elif abs(float(ln["unit_price"]) - want) > EPS:
                    problems.append(f"{ln['item']} sells at {ln['unit_price']} but the approved, quoted price at "
                                    f"{ln.get('qty')} is {want}")
            detail["rfq"] = True
        if qp and qp.get("total") is not None and abs(so_total - float(qp["total"])) > pol["total_tie_abs"] + EPS:
            problems.append(f"SO total {so_total} does not tie to the quote's {qp['total']}")
        if not _staged_after_def(conn, so):
            problems.append(f"{so} not staged in Odoo since its latest draft")
        cost_by_item: dict[str, float] = {}
        po_total = 0.0
        for po in pos:
            pp = db.latest_payload(conn, po) or {}
            for ln in pp.get("lines", []):
                cost_by_item[ln["item"].lower()] = float(ln["unit_cost"])
            po_total += _sum_lines(pp.get("lines", []), "unit_cost")
            if not _staged_after_def(conn, po):
                problems.append(f"{po} not staged in Odoo since its latest draft")
        for ln in so_lines:
            key = ln["item"].lower()
            if key not in cost_by_item and not ln.get("from_stock"):
                problems.append(f"no PO covers {ln['item']}")
            elif key in cost_by_item and cost_by_item[key] > float(ln["unit_price"]) + EPS:
                problems.append(f"{ln['item']} costs {cost_by_item[key]} but sells for {ln['unit_price']}")
        margin = (so_total - po_total) / so_total if so_total else 0.0
        detail.update({"so_total": so_total, "po_total": round(po_total, 2), "margin": round(margin, 4),
                       "margin_floor": pol["margin_floor"]})
        if margin + 1e-9 < pol["margin_floor"]:
            problems.append(f"margin {margin:.1%} is below the {pol['margin_floor']:.0%} floor")
    detail["problems"] = problems
    return _record(conn, "orders_drafts_valid", not problems, detail)


def orders_posted(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    _, so, pos = _orders_docs(conn)
    f = focus(conn, "orders")
    if f and f.startswith("po:"):
        targets = [f]
    else:
        targets = ([so] if so else []) + pos
    missing = [t for t in targets if not _posted_after_def(conn, t)]
    # Also passes when nothing anywhere is approved and waiting to be posted, so moving the focus on
    # to the next item can't strand the agent here. Any approved, unposted SO or PO keeps it failing.
    waiting = [r["entity"] for r in _queue(conn, "orders") if r["reason"] == "approved, needs posting"]
    ok = (bool(targets) and not missing) or not waiting
    return _record(conn, "orders_posted", ok, {"targets": targets, "not_posted": missing, "waiting": waiting})


# ---------------------------------------------------------------- billing

def billing_focus_is_so(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "billing")
    ok = any(r["entity"] == f and r["reason"] == "needs invoice" for r in _queue(conn, "billing"))
    return _record(conn, "billing_focus_is_so", ok, {"focus": f})


def billing_focus_is_bill(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "billing")
    ok = any(r["entity"] == f and r["reason"] in ("vendor bill to record", "needs 3-way match")
             for r in _queue(conn, "billing"))
    return _record(conn, "billing_focus_is_bill", ok, {"focus": f})


def _focus_bill(conn) -> str | None:
    f = focus(conn, "billing")
    if f and f.startswith("mail:"):
        b = _children(conn, f, "from_mail", "bill")
        return b[-1] if b else None
    return f if f and f.startswith("bill:") else None


def billing_invoice_posted(*_a: Any, **_k: Any) -> bool:
    """The focused SO's invoice is posted in Odoo and its untaxed amount equals the SO's."""
    conn = _conn()
    so = focus(conn, "billing")
    invs = _children(conn, so, "from_so", "invoice") if so else []
    problems = []
    if not invs:
        problems.append("no invoice linked from the SO")
    else:
        inv = invs[-1]
        posted = _last(conn, inv, "erp.posted")
        so_posted = _last(conn, so, "erp.posted")
        if not posted:
            problems.append(f"{inv} not posted")
        elif so_posted and abs(float(posted[1].get("amount_untaxed", 0)) -
                               float(so_posted[1].get("amount_untaxed", 0))) > 0.01 + EPS:
            problems.append(f"invoice untaxed {posted[1].get('amount_untaxed')} != SO "
                            f"{so_posted[1].get('amount_untaxed')}")
    return _record(conn, "billing_invoice_posted", not problems, {"so": so, "invoices": invs, "problems": problems})


def billing_bill_matched(*_a: Any, **_k: Any) -> bool:
    """A 3-way match was computed after the latest bill draft and every line is within tolerance."""
    conn = _conn()
    pol = policy()
    bill = _focus_bill(conn)
    problems = []
    m = _last(conn, bill, "bill.matched", "bill.mismatch") if bill else None
    if not m or m[0] < _last_def(conn, bill):
        problems.append("no 3-way match since the latest bill draft")
    elif not m[1].get("lines"):
        problems.append("match has no lines")
    else:
        for ln in m[1]["lines"]:
            po_cost, billed = ln.get("po_unit_cost"), ln.get("billed_unit_cost")
            if billed is None:
                continue
            tol = price_tolerance(po_cost, pol)
            if abs(billed - po_cost) > tol + EPS:
                problems.append(f"{ln['item']}: billed {billed} vs PO {po_cost} (tolerance {tol:.2f})")
            if ln.get("billed_qty") and ln["billed_qty"] > ln.get("received", 0) + 1e-9:
                problems.append(f"{ln['item']}: billed {ln['billed_qty']} but received {ln.get('received')}")
        if m[1].get("unmatched_bill_lines"):
            problems.append("bill has lines that are not on the PO")
    return _record(conn, "billing_bill_matched", not problems, {"bill": bill, "problems": problems})


def billing_bill_posted(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    bill = _focus_bill(conn)
    ok = bool(bill) and _posted_after_def(conn, bill)
    return _record(conn, "billing_bill_posted", ok, {"bill": bill})


# ---------------------------------------------------------------- cash

def _cash_focus_reason(conn) -> tuple[str | None, str | None]:
    f = focus(conn, "cash")
    for r in _actionable(conn, "cash"):
        if r["entity"] == f:
            return f, r["reason"]
    return f, None


def cash_focus_is_receipt(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f, reason = _cash_focus_reason(conn)
    return _record(conn, "cash_focus_is_receipt", reason in ("bank receipt to match", "unapplied cash"),
                   {"focus": f, "reason": reason})


def cash_focus_is_payable(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f, reason = _cash_focus_reason(conn)
    return _record(conn, "cash_focus_is_payable", reason == "open payable", {"focus": f, "reason": reason})


def cash_focus_is_approved_payment(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f, reason = _cash_focus_reason(conn)
    return _record(conn, "cash_focus_is_approved_payment", reason == "approved, needs posting",
                   {"focus": f, "reason": reason})


def cash_payment_applied(*_a: Any, **_k: Any) -> bool:
    """The receipt became a payment, and every dollar is either applied or explained."""
    conn = _conn()
    f = focus(conn, "cash")
    pay = f if f and f.startswith("payment:") else None
    if f and f.startswith("banktxn:"):
        ps = _children(conn, f, "from_bank", "payment")
        pay = ps[-1] if ps else None
    problems = []
    if not pay:
        problems.append("no payment recorded from the bank receipt")
    else:
        rec = _last(conn, pay, "payment.received")
        total_applied = round(sum(float(l["amount"]) for l in db.rows(
            conn, "SELECT amount FROM doc_links WHERE src = ? AND rel = 'applies_to'", [pay])), 2)
        received = float(rec[1].get("total", 0)) if rec else 0.0
        explained = _last(conn, pay, "payment.short", "payment.over", "payment.unmatched")
        if not _last(conn, pay, "payment.applied") and not _held(conn, pay):
            problems.append("payment not applied (and not held by the controller)")
        if abs(received - total_applied) > 0.01 + EPS and not explained:
            problems.append(f"received {received} but applied {total_applied}, and nothing explains the difference")
    return _record(conn, "cash_payment_applied", not problems, {"focus": f, "payment": pay, "problems": problems})


def cash_billpayment_proposed(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "cash")
    drafts = _children(conn, f, "pays", "billpayment") if f else []
    ok = bool(drafts) and bool(_last(conn, drafts[-1], "approval.requested"))
    return _record(conn, "cash_billpayment_proposed", ok, {"bill": f, "drafts": drafts})


def cash_billpayment_done(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "cash")
    ok = bool(f) and bool(_last(conn, f, "billpayment.sent")) and _posted_after_def(conn, f)
    return _record(conn, "cash_billpayment_done", ok, {"focus": f})


# ---------------------------------------------------------------- reconcile

def recon_ok(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    last = _last(conn, "recon:books", "recon.result")
    last_money = conn.execute("SELECT MAX(seq) FROM events WHERE kind IN ('je.posted','bank.transaction')"
                              ).fetchone()[0] or 0
    ok = bool(last) and last[1].get("ok") is True and last[0] > last_money
    return _record(conn, "recon_ok", ok, {"result_seq": last and last[0], "last_money_seq": last_money})


def recon_findings_recorded(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    last = _last(conn, "recon:books", "recon.result")
    if not last:
        return _record(conn, "recon_findings_recorded", False, {"problem": "no recon.result"})
    n_diff = len(last[1].get("differences", []))
    n_find = conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'recon.finding' AND seq > ?",
                          (last[0],)).fetchone()[0]
    return _record(conn, "recon_findings_recorded", n_find >= n_diff,
                   {"differences": n_diff, "findings": n_find})


# ---------------------------------------------------------------- idle

def desk_idle(*_a: Any, **_k: Any) -> bool:
    """No real work anywhere on the desk for policy idle_minutes: time for every driver to stop."""
    from datetime import datetime, timedelta, timezone

    conn = _conn()
    minutes = float(policy().get("idle_minutes", 15))
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="milliseconds")
    since = since.replace("+00:00", "Z")
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE ts > ? AND kind NOT IN ('check.result', 'note.focus')"
        " AND entity NOT LIKE 'tracker:%' AND entity NOT LIKE 'check:%'", (since,)).fetchone()
    busy = row[0]
    open_work = {d: len(_actionable(conn, d)) for d in DRIVERS if d != "reconcile"}
    ok = busy == 0 and not any(open_work.values())
    return _record(conn, "desk_idle", ok, {"events_in_window": busy, "minutes": minutes, "open": open_work})


def controller_idle(*_a: Any, **_k: Any) -> bool:
    """Benchmark controller may stop: no open approval request, and the desk is idle."""
    conn = _conn()
    open_reqs = 0
    for ent, seq in conn.execute("SELECT entity, MAX(seq) FROM events WHERE kind = 'approval.requested' GROUP BY entity"):
        if decision_after(conn, ent, seq) is None:
            open_reqs += 1
    idle = desk_idle()
    return _record(conn, "controller_idle", open_reqs == 0 and idle, {"open_requests": open_reqs, "desk_idle": idle})


# ---------------------------------------------------------------- pricing (RFQs: judge, model, finance, quote)

def _latest_seq(conn, entity: str, *kinds: str) -> int:
    marks = ",".join("?" * len(kinds))
    return conn.execute(f"SELECT COALESCE(MAX(seq), 0) FROM events WHERE entity = ? AND kind IN ({marks})",
                        (entity, *kinds)).fetchone()[0]


def _pricing_reason(conn) -> tuple[str | None, str | None]:
    f = focus(conn, "pricing")
    r = next((x["reason"] for x in _queue(conn, "pricing") if x["entity"] == f), None)
    return f, r


def pricing_focus_is_rfq(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f, r = _pricing_reason(conn)
    return _record(conn, "pricing_focus_is_rfq", r in ("needs pricing", "finance rejected, re-price"),
                   {"focus": f, "reason": r})


def pricing_focus_is_approved(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f, r = _pricing_reason(conn)
    return _record(conn, "pricing_focus_is_approved", r == "approved, quote to send", {"focus": f, "reason": r})


def pricing_assessed(*_a: Any, **_k: Any) -> bool:
    """The focus has an assessment newer than its latest extraction (and any rejection), with three bounded,
    evidenced calls, recorded by the pricing role."""
    from pricing.features import assessment_problems
    conn = _conn()
    f = focus(conn, "pricing")
    detail: dict = {"focus": f}
    ok = False
    if f:
        since = max(_latest_seq(conn, f, "quote.extracted", "quote.clarified", "quote.amended"),
                    _latest_seq(conn, f, "approval.rejected"))
        row = conn.execute("SELECT seq, role, payload FROM events WHERE entity = ? AND kind = 'pricing.assessment'"
                           " ORDER BY seq DESC LIMIT 1", (f,)).fetchone()
        if not row or row[0] <= since:
            detail["problem"] = "no pricing.assessment since the latest extraction or rejection"
        elif row[1] != "pricing":
            detail["problem"] = f"the assessment was recorded by role {row[1]!r}, not pricing"
        else:
            problems = assessment_problems(json.loads(row[2]))
            detail["problems"] = problems
            ok = not problems
    return _record(conn, "pricing_assessed", ok, detail)


def pricing_recommended(*_a: Any, **_k: Any) -> bool:
    """The model's recommendation is built from the latest assessment and the latest extraction."""
    conn = _conn()
    f = focus(conn, "pricing")
    detail: dict = {"focus": f}
    ok = False
    if f:
        rec = _last(conn, f, "pricing.recommended")
        a = _latest_seq(conn, f, "pricing.assessment")
        q = _latest_seq(conn, f, "quote.extracted", "quote.clarified", "quote.amended")
        if not rec or rec[0] < a:
            detail["problem"] = "no recommendation since the latest assessment: run python -m pricing.model recommend"
        else:
            inputs = rec[1].get("inputs", {})
            ok = inputs.get("assessment_seq") == a and inputs.get("quote_seq") == q
            detail.update({"assessment_seq": a, "quote_seq": q, "used": inputs})
    return _record(conn, "pricing_recommended", ok, detail)


def pricing_report_sent(*_a: Any, **_k: Any) -> bool:
    """Finance was emailed the latest recommendation, and the request for its decision is recorded."""
    conn = _conn()
    f = focus(conn, "pricing")
    detail: dict = {"focus": f}
    ok = False
    if f:
        rec = _latest_seq(conn, f, "pricing.recommended")
        req = conn.execute("SELECT seq, payload FROM events WHERE entity = ? AND kind = 'approval.requested'"
                           " AND json_extract(payload, '$.review_state') = 'finance_review' AND seq > ?"
                           " ORDER BY seq DESC LIMIT 1", (f, rec)).fetchone()
        if req:
            p = json.loads(req[1])
            mail = _last(conn, p.get("report_mail", ""), "mail.sent")
            ok = bool(mail) and mail[1].get("purpose") == "pricing_report" and \
                mail[1].get("recommendation_seq") == rec == p.get("recommendation_seq")
            detail.update({"request_seq": req[0], "report_mail": p.get("report_mail")})
        else:
            detail["problem"] = "no finance_review request after the latest recommendation: run python -m pricing.send report"
    return _record(conn, "pricing_report_sent", ok, detail)


def pricing_quote_sent(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    f = focus(conn, "pricing")
    ok = False
    detail: dict = {"focus": f}
    if f:
        appr = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE entity = ? AND kind = 'approval.granted'"
                            " AND role = 'finance'", (f,)).fetchone()[0]
        sent = _last(conn, f, "quote.sent")
        ok = bool(appr) and bool(sent) and sent[0] > appr and sent[1].get("approval_seq") == appr
        detail.update({"approval_seq": appr, "quote_sent_seq": sent and sent[0]})
    return _record(conn, "pricing_quote_sent", ok, detail)


def approved_unit_price(conn, quote: str, mpn: str, qty: float) -> float | None:
    """The price the customer was quoted (finance-approved) for this part at this quantity: the tier at or below it."""
    sent = _last(conn, quote, "quote.sent")
    if not sent:
        return None
    for ln in sent[1].get("lines", []):
        if str(ln.get("mpn", "")).upper() == str(mpn).upper():
            price = None
            for t in sorted(ln.get("schedule", []), key=lambda r: r["qty"]):
                if qty >= t["qty"]:
                    price = t["unit_price"]
            return price
    return None


# ---------------------------------------------------------------- architect

def architect_profile_written(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    problems = []
    for name in ("company_profile.md", "policy.json", "pipeline.json"):
        p = structure_dir() / name
        if not p.exists() or p.stat().st_size < 20:
            problems.append(f"{p.relative_to(ROOT).as_posix()} missing or empty")
    if not conn.execute("SELECT 1 FROM events WHERE kind LIKE 'structure.%' LIMIT 1").fetchone():
        problems.append("no structure.* events recorded")
    return _record(conn, "architect_profile_written", not problems, {"problems": problems})


def architect_all_validated(*_a: Any, **_k: Any) -> bool:
    conn = _conn()
    pipeline = structure_dir() / "pipeline.json"
    drivers = [d["name"] for d in json.loads(pipeline.read_text(encoding="utf-8")).get("drivers", [])
               if d.get("enabled", True)] if pipeline.exists() else []
    results = {d: proposal_ok(conn, d) for d in drivers}
    bad = {d: r[1] for d, r in results.items() if not r[0]}
    return _record(conn, "architect_all_validated", bool(drivers) and not bad, {"drivers": drivers, "invalid": bad})
