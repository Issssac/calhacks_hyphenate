"""The benchmark's stand-in for Redwood's finance team: it answers price reports by email, from the answer key.

    python -m bench.finance        answer every pricing report that hasn't been answered yet

What it does with a report (seed/worlds/redwood_scenarios.py, `expect`):
- `finance: "reject"`: REJECT with the scenario's reason (for example a part we can't supply the way the customer
  needs).
- a `finance_rule` (minimum margin for a category): REJECT with the rule's words if any tier breaks it.
- otherwise: compare each line's price at the requested quantity with the true best price under the hidden demand
  model. If it is more than 10% off, APPROVE WITH CHANGES to the best price; else APPROVE.

It writes its reply as a real email (In-Reply-To the report) into the desk's inbox, so the desk reads it the way it
would read a person's. Like the benchmark controller, it is a stand-in agent with an answer key, not a person.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
import time

from ledgerdb import db
from seed import mail, world
from seed.scenarios import BY_ID
from seed.sim import DESK_ADDR, SIM, _deliver, load as load_sim, scenario_of

TOLERANCE = 0.10


def true_best_price(cost: float, qty: float, calls: dict, repeat: bool, customer: str, floor: float) -> float:
    truth = importlib.import_module(f"seed.worlds.{world.NAME}_truth")
    lo = 1 / (1 - floor)
    best, best_v = lo, -1.0
    for i in range(600):
        m = math.exp(math.log(lo) + (math.log(3.0) - math.log(lo)) * i / 599)
        v = truth.p_win(m, qty, calls["urgency"], calls["wtp"], calls["growth"], repeat, customer) * (m - 1)
        if v > best_v:
            best, best_v = m, v
    return cost * best


def decide(conn, rec: dict, s: dict) -> tuple[str, list[str]]:
    """The reply's text lines for this recommendation: the first line is the decision."""
    exp = s.get("expect", {})
    if exp.get("finance") == "reject":
        return "reject", [f"REJECT: {exp.get('finance_reason', 'Not this one.')}", "", "Priya"]
    rule = exp.get("finance_rule")
    if rule:
        for ln in rec["lines"]:
            if ln.get("category") != rule["category"]:
                continue
            for r in ln.get("schedule", []):
                if r.get("unit_price") and r.get("margin") is not None and r["margin"] < rule["min_margin"] - 1e-9:
                    return "reject", [f"REJECT: {rule['reason']}", "", "Priya"]
    from pricing.features import is_repeat
    from pricing.model import history_path
    repeat = is_repeat(rec.get("customer"), history_path())
    changes = []
    for ln in rec["lines"]:
        r = ln.get("requested")
        if not r or not r.get("unit_price") or not r.get("unit_cost"):
            continue
        best = true_best_price(r["unit_cost"], ln["qty"], s.get("truth", {"urgency": "normal", "wtp": "normal",
                                                                          "growth": "flat"}),
                               repeat, rec.get("customer"), max(ln.get("margin_floor", 0.15), 0.15))
        if abs(r["unit_price"] / best - 1) > TOLERANCE:
            price = round(best, 4 if best < 1 else 2)
            changes.append(f"{ln['mpn']} {r['qty']}: {price}")
    if changes:
        return "approve_with_changes", ["APPROVE WITH CHANGES", *changes, "", "Priya"]
    return "approve", ["APPROVE", "", "Looks right. Priya"]


def respond(conn) -> list[dict]:
    st = load_sim()
    answered = {e["payload"].get("report_mail") for e in db.rows(
        conn, "SELECT payload FROM events WHERE kind = 'sim.finance_reply'")}
    out = []
    for m in db.rows(conn, "SELECT entity, payload FROM events WHERE kind = 'mail.sent' "
                           "AND json_extract(payload, '$.purpose') = 'pricing_report' ORDER BY seq"):
        if m["entity"] in answered:
            continue
        p = m["payload"]
        latest = conn.execute("SELECT MAX(seq) FROM events WHERE entity = ? AND kind = 'pricing.recommended'",
                              (p["rfq"],)).fetchone()[0]
        if p.get("recommendation_seq") != latest:
            continue  # a newer recommendation is on its way; answer that one
        rec = db.rows(conn, "SELECT payload FROM events WHERE seq = ?", [latest])[0]["payload"]
        sid = scenario_of(conn, p["rfq"], st)
        s = BY_ID.get(sid)
        if not s:
            continue
        decision, lines = decide(conn, rec, s)
        raw = mail.message(f"{world.FINANCE['name']} <{world.FINANCE['email']}>", DESK_ADDR, f"Re: {p['subject']}",
                           "\n".join(lines) + "\n\n> (report quoted below)\n", message_id=None,
                           in_reply_to=p["message_id_header"])
        where = _deliver(raw, f"finance-{sid}-{int(time.time())}.eml", False)
        db.append(conn, kind="sim.finance_reply", entity=f"sim:{sid}-finance-{latest}",
                  payload={"rfq": p["rfq"], "scenario": sid, "report_mail": m["entity"], "decision": decision,
                           "reply": lines, "recommendation_seq": latest, "delivered": where,
                           "via": "benchmark finance stand-in (answer key)"}, **SIM)
        out.append({"scenario": sid, "decision": decision})
    return out


def main() -> int:
    conn = db.connect()
    sys.stdout.write(json.dumps(respond(conn), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
