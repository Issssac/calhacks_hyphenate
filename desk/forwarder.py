"""The inbox forwarder: a plain Python loop (no model, no tokens) that feeds the workflows.

Each pass it:
  1. pulls new mail (Gmail if connected, plus the drop folder) and tracks Odoo and the bank,
     so no agent has to poll for them;
  2. works out which workflows have something they can act on now:
       - new actionable items in their queue,
       - a controller decision that arrived on one of their open requests,
       - open feedback (a correction to learn from);
  3. wakes only those workflows, with a short message saying what is new, and records
     every wake-up in the ledger as note.dispatch.

A workflow is woken once per new reason, not once per pass: the forwarder remembers what it
has already handed over (the dispatch log in the ledger), so an item waiting on someone else
doesn't wake anyone again.

    python -m desk.forwarder once              one pass, then exit
    python -m desk.forwarder run --every 20    loop
    python -m desk.forwarder run --dry-run     decide and print, wake nobody

How a workflow is woken is a pluggable Dispatcher (see desk/dispatch.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledgerdb import db  # noqa: E402
import checks.ledger_checks as lc  # noqa: E402

ACTOR = "forwarder"
WORKFLOWS = ("intake", "orders", "billing", "cash", "reconcile", "pricing", "controller")
# Review points a person answers some other way than the controller seat: finance replies to the report email.
NOT_FOR_CONTROLLER = {"finance_review"}


# ---------------------------------------------------------------- sensing

def sense(conn) -> dict:
    """Bring the outside world into the ledger. Each source is optional; failures are reported, not fatal."""
    out: dict[str, Any] = {}
    try:
        from connectors import inbox
        out["mail"] = len(inbox.pull(conn, {"actor": ACTOR, "role": "intake", "workflow": "forwarder"})["new"])
    except Exception as e:
        out["mail_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        from connectors import odoo
        out["odoo"] = odoo.track(conn, odoo.Odoo(), {"actor": ACTOR, "role": "tracker", "workflow": "forwarder"})["changes"]
    except Exception as e:
        out["odoo_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        from connectors import increase
        out["bank"] = increase.track(conn, increase.Increase(), {"actor": ACTOR, "role": "tracker",
                                                                 "workflow": "forwarder"})["new_transactions"]
    except (Exception, SystemExit) as e:
        out["bank_error"] = f"{type(e).__name__}: {e}"[:200]
    return out


# ---------------------------------------------------------------- deciding

def _undecided_requests(conn) -> list[dict]:
    rows = db.rows(conn, "SELECT seq, entity, workflow, payload FROM events WHERE kind = 'approval.requested' ORDER BY seq")
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["entity"]] = r
    return [r for r in latest.values() if lc.decision_after(conn, r["entity"], r["seq"]) is None]


def _decided_requests(conn) -> list[dict]:
    """Requests whose decision has arrived: the requesting workflow needs to act on it."""
    latest: dict[str, dict] = {}
    for r in db.rows(conn, "SELECT seq, entity, workflow, payload FROM events WHERE kind = 'approval.requested' ORDER BY seq"):
        latest[r["entity"]] = r
    out = []
    for r in latest.values():
        dec = lc.decision_unacted(conn, (r["seq"], r["entity"], r["payload"]), r["workflow"])
        if dec:
            out.append({**r, "decision": dec[0], "note": dec[1].get("note")})
    return out


def work(conn) -> dict[str, list[dict]]:
    """Everything each workflow could act on right now, as reasons with a stable key."""
    found: dict[str, list[dict]] = {w: [] for w in WORKFLOWS}
    for d in lc.DRIVERS:
        for item in lc._actionable(conn, d):
            found[d].append({"key": f"item:{item['entity']}:{item['reason']}", "what": f"{item['entity']} ({item['reason']})"})
        for fb in db.rows(conn, "SELECT entity, note FROM feedback_open WHERE driver = ?", [d]):
            found[d].append({"key": f"feedback:{fb['entity']}", "what": f"open feedback {fb['entity']}: {(fb['note'] or '')[:120]}"})
    for r in _decided_requests(conn):
        if r["workflow"] in found:
            who = "finance" if (r.get("payload") or {}).get("review_state") in NOT_FOR_CONTROLLER else "controller"
            found[r["workflow"]].append({"key": f"decision:{r['entity']}:{r['seq']}",
                                         "what": f"{who} {r['decision'].split('.')[-1]} {r['entity']}: {(r['note'] or '')[:120]}"})
    for r in _undecided_requests(conn):
        if (r.get("payload") or {}).get("review_state") in NOT_FOR_CONTROLLER:
            continue  # finance answers by email; desk/finance_reply.py records it
        found["controller"].append({"key": f"request:{r['entity']}:{r['seq']}",
                                    "what": f"{r['workflow']} asks about {r['entity']}"})
    return found


def already_dispatched(conn, workflow: str) -> set[str]:
    keys: set[str] = set()
    for r in db.rows(conn, "SELECT payload FROM events WHERE kind = 'note.dispatch' AND entity = ?", [f"dispatch:{workflow}"]):
        keys.update(r["payload"].get("keys", []))
    return keys


def plan(conn) -> dict[str, list[dict]]:
    """Only reasons nobody has been woken for yet."""
    todo = {}
    for wf, reasons in work(conn).items():
        seen = already_dispatched(conn, wf)
        new = [r for r in reasons if r["key"] not in seen]
        if new:
            todo[wf] = new
    return todo


def last_event(conn, workflow: str) -> str | None:
    row = conn.execute("SELECT json_extract(payload, '$.detail.eventId') FROM events WHERE kind = 'note.dispatch'"
                       " AND entity = ? ORDER BY seq DESC LIMIT 1", (f"dispatch:{workflow}",)).fetchone()
    return row[0] if row and row[0] else None


def message(workflow: str, reasons: list[dict]) -> str:
    lines = [f"New work for the {workflow} workflow (from the forwarder):"] + [f"- {r['what']}" for r in reasons[:20]]
    if len(reasons) > 20:
        lines.append(f"- … and {len(reasons) - 20} more (see `python -m ledgerdb queue {workflow}`)")
    lines.append("Start from your begin state; the ledger is the source of truth, this list is only the reason you were woken.")
    return "\n".join(lines)


# ---------------------------------------------------------------- one pass

def run_once(conn, dispatcher, dry_run: bool = False) -> dict:
    sensed = sense(conn)
    try:
        from desk import finance_reply
        decided = finance_reply.process(conn)
        if decided:
            sensed["finance_replies"] = decided
    except Exception as e:  # a reply that can't be read must not stop the desk
        sensed["finance_reply_error"] = f"{type(e).__name__}: {e}"[:200]
    if conn.execute("SELECT 1 FROM events WHERE kind = 'quote.sent' LIMIT 1").fetchone():
        try:
            from pricing.model import record_outcomes
            settled = record_outcomes(conn)
            if settled:
                sensed["quote_outcomes"] = settled
        except Exception as e:
            sensed["outcome_error"] = f"{type(e).__name__}: {e}"[:200]
    todo = plan(conn)
    woken = {}
    for wf, reasons in todo.items():
        text = message(wf, reasons)
        if dry_run:
            woken[wf] = {"dry_run": True, "reasons": [r["what"] for r in reasons]}
            continue
        result = dispatcher.wake(wf, text, [r["key"] for r in reasons], last_event(conn, wf))
        if result.get("busy"):
            # Its current run will find this work in the ledger; nothing is recorded, so if any of it
            # is still there when that run ends, the next pass wakes the workflow again.
            woken[wf] = {"skipped": "previous run still going"}
            continue
        woken[wf] = result
        if result.get("ok"):
            db.append(conn, actor=ACTOR, role="tracker", kind="note.dispatch", entity=f"dispatch:{wf}",
                      payload={"keys": [r["key"] for r in reasons], "message_sha": hashlib.sha256(text.encode()).hexdigest()[:16],
                               "via": result.get("via"), "detail": result.get("detail")}, workflow="forwarder")
    return {"sensed": sensed, "woken": woken}


def main(argv: list[str] | None = None) -> int:
    from desk.dispatch import make_dispatcher

    p = argparse.ArgumentParser(prog="desk.forwarder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("once", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--dispatcher", default=None, help="see desk/dispatch.py (default: from structure/pipeline.json)")
        if name == "run":
            sp.add_argument("--every", type=float, default=20.0)
            sp.add_argument("--passes", type=int, default=0, help="0 = forever")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    conn = db.connect()
    db.init(conn)
    dispatcher = make_dispatcher(args.dispatcher)
    n = 0
    while True:
        out = run_once(conn, dispatcher, args.dry_run)
        if out["woken"] or args.cmd == "once":
            sys.stdout.write(json.dumps({"ts": db.now(), **out}, default=str) + "\n")
            sys.stdout.flush()
        n += 1
        if args.cmd == "once" or (args.passes and n >= args.passes):
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
