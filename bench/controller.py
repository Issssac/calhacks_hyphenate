"""The benchmark controller: answers the desk's approval requests from the answer key.

The desk's job is to ask the right questions at the right moments; what a person answers is
not what we are measuring. So for benchmarking, a stand-in controller answers every request
the way the answer key (seed/scenarios.py) says a correct controller would, and the scorer
then checks the desk did the right thing with that answer.

    python -m bench.controller pending          open requests, each with what the key says
    python -m bench.controller decide <entity> approve|reject --note "..." [--also po:PO-1 ...]
    python -m bench.controller auto             decide everything the key is sure about

Decisions are recorded as the controller itself (actor `model:controller`, role `controller`).
The ledger still refuses an approval from anyone who drafted the item.
"""

from __future__ import annotations

import argparse
import json
import sys

from ledgerdb import db
from seed import world
from seed.scenarios import BY_ID, effective_expect
from seed.sim import load as load_sim, scenario_of

CONTROLLER = "model:controller"
ACTOR = "controller-agent"


def _lines(lines):
    return {(l["item"].lower(), round(float(l.get("qty", 0)), 3)): round(float(l.get("unit_price", l.get("unit_cost", 0))), 2)
            for l in lines or []}


def _expect_lines(exp):
    return {(i.lower(), round(float(q), 3)): round(float(p), 2) for i, q, p in exp}


def _fmt(exp):
    return "; ".join(f"{q:g} x {i} at ${p:.2f}" for i, q, p in exp)


def pending(conn) -> list[dict]:
    reqs = db.rows(conn, "SELECT seq, entity, workflow, payload FROM events WHERE kind = 'approval.requested' ORDER BY seq")
    latest: dict[str, dict] = {}
    for r in reqs:
        latest[r["entity"]] = r  # the newest request per item
    out = []
    for ent, r in latest.items():
        decided = conn.execute("SELECT 1 FROM events WHERE entity = ? AND seq > ? AND kind IN"
                               " ('approval.granted','approval.rejected')", (ent, r["seq"])).fetchone()
        if not decided:
            out.append({"entity": ent, "workflow": r["workflow"], "seq": r["seq"], "request": r["payload"],
                        "key": suggest(conn, ent, r["workflow"], r["payload"])})
    return out


def review_revision(conn, driver: str) -> dict:
    """Rule-based review of a proposed workflow revision (the benchmark has no person to read it).

    Rejects a revision that would fail promotion anyway (bad structure, removes a control,
    changes an existing policy key), or that doesn't answer an open feedback item.
    """
    import json as _json
    from workflows import build

    prop = build.PROPOSED / f"{driver}.json"
    if not prop.exists():
        return {"decision": "reject", "note": f"There is no proposal file for {driver}."}
    spec = _json.loads(prop.read_text(encoding="utf-8"))
    problems = build.structural_problems(spec)
    if build.live_path(driver).exists():
        problems += build.weakened(_json.loads(build.live_path(driver).read_text(encoding="utf-8")), spec)
    patch = build.PROPOSED / f"policy.{driver}.json"
    if patch.exists():
        problems += build.policy_patch_problems(_json.loads(patch.read_text(encoding="utf-8")))
    proposed = db.rows(conn, "SELECT seq FROM events WHERE entity = ? AND kind = 'workflow.proposed' ORDER BY seq DESC LIMIT 1",
                       [f"workflow:{driver}"])
    addressed = [r["dst"] for r in db.rows(conn, "SELECT dst FROM doc_links WHERE src = ? AND rel = 'addresses'"
                                                 " AND seq = ?", [f"workflow:{driver}", proposed[0]["seq"]])] if proposed else []
    open_fb = {r["entity"] for r in db.rows(conn, "SELECT entity FROM feedback_open WHERE driver = ?", [driver])}
    if not addressed:
        problems.append("it doesn't say which feedback it answers")
    elif not any(a in open_fb for a in addressed):
        problems.append(f"the feedback it answers ({', '.join(addressed)}) is already closed")
    if problems:
        return {"decision": "reject", "note": "Not adopted: " + "; ".join(problems) + "."}
    return {"decision": "approve", "note": f"Adopted: answers {', '.join(addressed)}; adds controls without removing any."}


def suggest(conn, entity: str, workflow: str, req: dict) -> dict:
    """What the answer key says a correct controller decides here."""
    state = req.get("review_state", "")
    sid = scenario_of(conn, entity, load_sim())
    s = BY_ID.get(sid) or {}
    quote = None
    if entity.startswith("so:"):
        quote = (db.links_of(conn, entity, "from_quote") or [None])[0]
    elif entity.startswith("quote:"):
        quote = entity
    amended = bool(quote) and conn.execute(
        "SELECT 1 FROM events WHERE entity = ? AND kind = 'quote.amended'", (quote,)).fetchone() is not None
    exp = effective_expect(s, amended)
    items = [i for i in req.get("items", []) if isinstance(i, str) and i != entity]
    base = {"scenario": sid, "scenario_title": s.get("title"), "also": items}

    if entity.startswith("workflow:") or state == "adopt":
        return {**base, **review_revision(conn, entity.split(":", 1)[1])}

    if workflow == "intake" and state == "clarify":
        if exp.get("so"):
            rec = req.get("recommendation")
            note = f"Correct values: {_fmt(exp['so'])}. The formal document (the PDF) governs over the email summary."
            ok = isinstance(rec, (dict, str)) and all(f"{p:.2f}" in json.dumps(rec) for _, _, p in exp["so"])
            return {**base, "decision": "approve" if ok else "reject", "note": "Agreed, use the PDF." if ok else note}
        return {**base, "decision": "approve", "note": "Agreed."}

    if workflow == "orders":
        so = entity if entity.startswith("so:") else None
        sop = db.latest_payload(conn, so) if so else {}
        if exp.get("orders_flagged"):
            return {**base, "decision": "reject",
                    "note": "We can't source a custom davit. Cancel this sales order and tell the customer we "
                            "don't supply it."}
        amendment = next((x for x in BY_ID.values() if x.get("reply_to") == sid
                          and x["expect"].get("addon_so")), None)
        if so and quote and amendment:
            sos = [r["src"] for r in db.rows(conn, "SELECT DISTINCT src FROM doc_links WHERE rel = 'from_quote'"
                                                   " AND dst = ? AND src LIKE 'so:%' ORDER BY seq", [quote])]
            kinds = [r["kind"] for r in db.rows(conn, "SELECT kind FROM events WHERE entity = ? ORDER BY seq", [so])]
            posted_then_amended = "erp.posted" in kinds and "so.amended" in kinds[kinds.index("erp.posted"):]
            if sos and so != sos[0]:  # an add-on order raised for the amendment
                ok = _lines(sop.get("lines")) == _expect_lines(amendment["expect"]["addon_so"])
                return {**base, "scenario": amendment["id"], "decision": "approve" if ok else "reject",
                        "note": "Approved: add-on order for the amendment." if ok else
                                f"The add-on order should be exactly: {_fmt(amendment['expect']['addon_so'])}."}
            if posted_then_amended:
                return {**base, "scenario": amendment["id"], "decision": "reject",
                        "note": amendment["expect"]["controller_rejects_amendment"]}
        if exp.get("so") and so:
            if _lines(sop.get("lines")) == _expect_lines(exp["so"]):
                return {**base, "decision": "approve", "note": "Approved."}
            note = exp.get("controller_rejects") or f"Wrong lines. They should be: {_fmt(exp['so'])}."
            return {**base, "decision": "reject", "note": note}
        return {**base, "decision": "approve", "note": "Approved."}

    if workflow == "billing":
        if state == "bill_review":
            return {**base, "decision": "approve",
                    "note": "Accept the variance: the vendor confirmed a price increase. Update their price list."}
        return {**base, "decision": "approve", "note": "Approved."}

    if workflow == "cash":
        if state == "payment_review":
            pay = db.latest_payload(conn, entity) or {}
            vb = pay.get("vendor_bank") or {}
            v = next((x for x in world.VENDORS if x["name"] == pay.get("vendor")), None)
            ok = bool(v) and vb.get("account_number") == v["bank"]["account"] and vb.get("routing_number") == v["bank"]["routing"]
            return {**base, "decision": "approve" if ok else "reject",
                    "note": "Approved: the bank details match the vendor master." if ok else
                            "Rejected: those bank details are not the ones in the vendor master."}
        return {**base, "decision": "approve",
                "note": "Agreed with your recommendation (hold anything that isn't customer money)."}

    if workflow == "reconcile":
        return {**base, "decision": "approve", "note": "Findings accepted."}
    return {**base, "decision": "approve", "note": "Approved."}


def decide(conn, entity: str, decision: str, note: str, also: list[str] | None = None) -> list[dict]:
    kind = "approval.granted" if decision == "approve" else "approval.rejected"
    out = []
    for e in [entity] + [a for a in (also or []) if a != entity]:
        out.append(db.append(conn, actor=ACTOR, role="controller", on_behalf_of=CONTROLLER, kind=kind, entity=e,
                             payload={"note": note, "via": "benchmark controller (answer key)"}, workflow="controller"))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bench.controller", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending")
    d = sub.add_parser("decide")
    d.add_argument("entity")
    d.add_argument("decision", choices=["approve", "reject"])
    d.add_argument("--note", required=True)
    d.add_argument("--also", action="append", default=[])
    sub.add_parser("auto")
    args = p.parse_args(argv)
    conn = db.connect()
    out_ = lambda o: sys.stdout.write(json.dumps(o, indent=2, default=str) + "\n")
    try:
        if args.cmd == "pending":
            out_(pending(conn))
        elif args.cmd == "decide":
            out_(decide(conn, args.entity, args.decision, args.note, args.also))
        else:
            done = []
            for r in pending(conn):
                k = r["key"]
                done.append({"entity": r["entity"], "decision": k["decision"], "note": k["note"],
                             "events": decide(conn, r["entity"], k["decision"], k["note"], k.get("also"))})
            out_(done)
    except db.LedgerError as e:
        out_({"error": str(e)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
