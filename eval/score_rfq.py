"""Scoring for an RFQ desk (world FLOW "rfq", e.g. Redwood): pricing, finance's decision, the quote, then the order
through to cash. Read from the ledger only; the hidden demand model is used to measure how good the prices were.

Pricing quality is reported as **regret**: how much expected profit the price leaves on the table against the
best price under the true demand model (0% = optimal). It's checked for the model's recommendation and for the
price finance approved.
"""

from __future__ import annotations

import importlib
from typing import Any

from ledgerdb import db
from seed import world

PASS, FAIL, PENDING = "pass", "fail", "pending"
REGRET_OK = 0.05      # the approved price may give up at most 5% of the best expected profit
REGRET_MODEL = 0.10   # the model's own recommendation, before finance: at most 10%


def _rows(conn, sql, params=()):
    return db.rows(conn, sql, list(params))


def _true_profit(markup: float, qty: float, calls: dict, repeat: bool, customer: str) -> float:
    truth = importlib.import_module(f"seed.worlds.{world.NAME}_truth")
    return truth.p_win(markup, qty, calls["urgency"], calls["wtp"], calls["growth"], repeat, customer) * (markup - 1)


def regret(price: float, cost: float, qty: float, calls: dict, repeat: bool, customer: str, floor: float) -> float:
    from bench.finance import true_best_price
    best = true_best_price(cost, qty, calls, repeat, customer, floor)
    b = _true_profit(best / cost, qty, calls, repeat, customer)
    return max(0.0, 1 - _true_profit(price / cost, qty, calls, repeat, customer) / b) if b > 0 else 0.0


def score_rfq(conn, s: dict, sim: dict, helpers: dict) -> dict:
    exp = s.get("expect", {})
    res: dict = {"id": s["id"], "title": s["title"], "checks": []}

    def add(name, status, detail=""):
        res["checks"].append({"check": name, "status": status, "detail": detail})

    sent = sim["sent"].get(s["id"])
    if not sent:
        add("sent", PENDING, "not sent yet")
        return res
    mail = helpers["mail_for"](conn, sent["file"])
    if not mail:
        add("intake", PENDING, "not pulled yet")
        return res
    quotes = helpers["children"](conn, mail, "from_mail", "quote")
    rfq = quotes[-1] if quotes else None
    if not rfq:
        add("intake: RFQ extracted", PENDING, "no quote created yet")
        return res
    kinds = helpers["kinds"](conn, rfq)
    ext = _rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind IN ('quote.extracted','quote.amended') "
                      "ORDER BY seq DESC LIMIT 1", [rfq])
    if exp.get("intake") == "amended":
        add("intake: revision applied", PASS if "quote.amended" in kinds else PENDING, rfq)
    else:
        ok = bool(ext) and bool(ext[0]["payload"].get("rfq"))
        add("intake: RFQ extracted", PASS if ok else (FAIL if ext else PENDING), rfq)

    recs = _rows(conn, "SELECT seq, payload FROM events WHERE entity = ? AND kind = 'pricing.recommended' ORDER BY seq",
                 [rfq])
    if not recs:
        add("priced", PENDING, "no recommendation yet")
        return res
    first, rec = recs[0]["payload"], recs[-1]["payload"]
    got = sorted((ln["mpn"].upper(), int(ln["qty"])) for ln in rec["lines"])
    want = sorted((m.upper(), int(q)) for m, q in exp.get("lines", []))
    add("priced the requested parts and quantities", PASS if got == want else FAIL, f"{got}")
    assess = _rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind = 'pricing.assessment' ORDER BY seq DESC "
                         "LIMIT 1", [rfq])
    for call, value in exp.get("calls", {}).items():
        have = assess[0]["payload"]["calls"].get(call, {}).get("value") if assess else None
        add(f"judgement: {call}", PASS if have == value else FAIL, f"{have} (expected {value})")
    for mpn, text in exp.get("flags", {}).items():
        ln = next((x for x in first["lines"] if x["mpn"].upper() == mpn.upper()), None)
        flags = " ".join((ln or {}).get("requested", {}).get("flags", []) if ln and ln.get("requested") else [])
        add(f"flag on {mpn}: {text}", PASS if text in flags else FAIL, flags or "no flags")

    reports = _rows(conn, "SELECT payload FROM events WHERE kind = 'mail.sent' AND json_extract(payload,'$.rfq') = ? "
                          "AND json_extract(payload,'$.purpose') = 'pricing_report'", [rfq])
    add("report emailed to finance", PASS if reports else PENDING, f"{len(reports)} report(s)")
    decisions = _rows(conn, "SELECT kind, payload FROM events WHERE entity = ? AND role = 'finance' "
                            "AND kind IN ('approval.granted','approval.rejected') ORDER BY seq", [rfq])
    rejected = [d for d in decisions if d["kind"] == "approval.rejected"]
    granted = [d for d in decisions if d["kind"] == "approval.granted"]
    learn = exp.get("learn")
    if exp.get("finance") == "reject":
        add("finance rejected, by email", PASS if rejected else PENDING, rejected[0]["payload"].get("note", "") if rejected else "")
    elif learn == "first":
        add("finance corrected the desk (rule)", PASS if rejected else (FAIL if granted else PENDING),
            rejected[0]["payload"].get("note", "")[:80] if rejected else "no rejection")
        adopted = _rows(conn, "SELECT 1 FROM events e WHERE e.kind = 'workflow.adopted' AND e.entity = 'workflow:pricing' "
                              "AND EXISTS (SELECT 1 FROM doc_links l WHERE l.seq = e.seq AND l.rel = 'addresses')")
        add("pricing adopted a revision for it", PASS if adopted else PENDING, "")
        add("re-priced and approved", PASS if granted else PENDING, "")
    elif learn == "caught":
        rule = exp["finance_rule"]
        margins = [r["margin"] for ln in first["lines"] if ln.get("category") == rule["category"]
                   for r in ln["schedule"] if r.get("margin") is not None]
        ok = margins and min(margins) >= rule["min_margin"] - 1e-9
        add("priced right first time (finance's rule)", PASS if ok else FAIL, f"lowest margin {min(margins):.1%}" if margins else "")
        add("approved without a correction", FAIL if rejected else (PASS if granted else PENDING), "")
    else:
        add("finance approved, by email", PASS if granted else (FAIL if rejected else PENDING),
            granted[-1]["payload"].get("decision", "") if granted else "")

    # How good were the prices? The model's first recommendation, and what finance approved.
    from pricing.features import is_repeat
    from pricing.model import history_path
    truth_calls = s.get("truth", {"urgency": "normal", "wtp": "normal", "growth": "flat"})
    repeat = is_repeat(rec.get("customer"), history_path())

    def worst_regret(lines, prices):
        worst = 0.0
        for ln in lines:
            r = ln.get("requested") or {}
            p = prices.get(ln["mpn"]) if prices else r.get("unit_price")
            if not p or not r.get("unit_cost"):
                continue
            worst = max(worst, regret(p, r["unit_cost"], ln["qty"], truth_calls, repeat, rec.get("customer"),
                                      ln.get("margin_floor", 0.15)))
        return worst

    if exp.get("finance") != "reject" and not learn:
        w = worst_regret(first["lines"], None)
        add("model's price vs the true optimum", PASS if w <= REGRET_MODEL else FAIL, f"regret {w:.1%}")
    if granted and exp.get("finance") != "reject":
        appr = granted[-1]["payload"].get("approved") or []
        prices = {a["mpn"]: next((t["unit_price"] for t in a["schedule"] if t["qty"] == a["qty"]), None) for a in appr}
        w = worst_regret(rec["lines"], prices or None)
        add("approved price vs the true optimum", PASS if w <= REGRET_OK else FAIL, f"regret {w:.1%}")

    if exp.get("declined_to_quote"):
        add("declined to quote (no quote sent)", PASS if "quote.rejected" in kinds and "quote.sent" not in kinds
            else (FAIL if "quote.sent" in kinds else PENDING), "")
        return res
    qs = _rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind = 'quote.sent' ORDER BY seq DESC LIMIT 1", [rfq])
    if not qs:
        add("quote sent to the customer", PENDING, "")
        return res
    ok = True
    if granted:
        appr = {a["mpn"]: a["schedule"] for a in granted[-1]["payload"].get("approved") or []}
        for ln in qs[0]["payload"]["lines"]:
            if ln["mpn"] in appr:
                ok &= [(t["qty"], t["unit_price"]) for t in ln["schedule"]] == \
                      [(t["qty"], t["unit_price"]) for t in appr[ln["mpn"]]]
    add("quote sent at the approved prices", PASS if ok else FAIL, qs[0]["payload"].get("to", ""))

    answer = sim.get("answered", {}).get(rfq)
    if not answer:
        add("customer answered", PENDING, "")
        return res
    if not answer["taken"]:
        lost = _rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind = 'pricing.outcome'", [rfq])
        add("customer declined: outcome learned", PASS if lost else PENDING,
            lost[0]["payload"]["outcome"] if lost else "")
        return res
    add("customer ordered", PASS, f"{answer['po']}: {len(answer['taken'])} line(s)")
    sos = helpers["children"](conn, rfq, "from_quote", "so")
    if not sos:
        add("order at the approved price", PENDING, "no SO yet")
        return res
    so = sos[0]
    sop = db.latest_payload(conn, so) or {}
    have = sorted((str(l.get("mpn") or l["item"]).upper(), int(float(l["qty"])), round(float(l["unit_price"]), 4))
                  for l in sop.get("lines", []))
    want = sorted((m.upper(), int(n), round(float(p), 4)) for m, n, p in answer["taken"])
    add("order at the approved price", PASS if have == want else FAIL, f"{so}")
    from bench.controller import _rfq_order_decision
    d = _rfq_order_decision(conn, so, rfq)
    add("bought from the sourcing rule's supplier", PASS if d["decision"] == "approve" else FAIL, d["note"][:90])
    posted = _rows(conn, "SELECT 1 FROM events WHERE entity = ? AND kind = 'erp.posted'", [so])
    add("order confirmed in Odoo", PASS if posted else PENDING, "")
    invs = helpers["children"](conn, so, "from_so", "invoice")
    inv_posted = invs and _rows(conn, "SELECT payload FROM events WHERE entity = ? AND kind = 'erp.posted'", [invs[-1]])
    add("invoiced", PASS if inv_posted else PENDING, invs[-1] if invs else "")
    bills = [b for po in helpers["children"](conn, so, "for_so", "po") for b in helpers["children"](conn, po, "from_po", "bill")]
    matched = [b for b in bills if "bill.matched" in helpers["kinds"](conn, b)]
    add("vendor bill 3-way matched", PASS if matched else PENDING, f"{len(matched)}/{len(bills)}")
    if invs:
        pays = _rows(conn, "SELECT src FROM doc_links WHERE rel = 'applies_to' AND dst = ? AND src LIKE 'payment:%'", [invs[-1]])
        add("cash applied", PASS if pays else PENDING, "")
    return res


def desk_checks(conn) -> list[dict]:
    """Checks about the desk as a whole rather than one scenario."""
    out = []
    fits = _rows(conn, "SELECT payload FROM events WHERE kind = 'pricing.model_fitted' ORDER BY seq")
    outcomes = _rows(conn, "SELECT seq FROM events WHERE kind = 'pricing.outcome'")
    if outcomes:
        refit = fits and fits[-1]["payload"]["n"] > fits[0]["payload"]["n"]
        out.append({"check": "model refit on the desk's own outcomes", "status": PASS if refit else FAIL,
                    "detail": f"{fits[0]['payload']['n']} -> {fits[-1]['payload']['n']} quotes" if fits else "never fit"})
    return out
