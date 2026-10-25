"""Reconciliation, in code: three views of the same money must agree.

1. Our journal (je.posted, mirrored from what Odoo booked for us) vs Odoo's own
   trial balance. A difference means someone booked in Odoo outside the desk,
   or the desk booked something it didn't record.
2. Money in at the bank (bank.transaction from Increase) vs payments recorded
   from it. A difference is cash nobody has matched yet.
3. Increase's reported balance vs the transactions we have tracked.

    python -m recon run --actor reconcile-1 --role reconcile

Writes one recon.result on entity recon:books: {ok, differences: [...]}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ledgerdb import db

TOL = 0.01


def books(conn, odoo_tb: list[dict]) -> list[dict]:
    ours = {r["account"]: r for r in db.rows(conn, "SELECT * FROM trial_balance")}
    theirs = {r["account"]: r for r in odoo_tb}
    diffs = []
    for acct in sorted(set(ours) | set(theirs)):
        a = round(float(ours.get(acct, {}).get("balance", 0) or 0), 2)
        b = round(float(theirs.get(acct, {}).get("balance", 0) or 0), 2)
        if abs(a - b) > TOL:
            diffs.append({"kind": "books", "account": acct, "ours": a, "odoo": b, "diff": round(a - b, 2)})
    return diffs


def bank(conn, increase_balance: float | None) -> tuple[list[dict], dict]:
    inflows = db.rows(conn, "SELECT entity, json_extract(payload, '$.amount') AS amount FROM events"
                            " WHERE kind = 'bank.transaction'")
    tracked = round(sum(float(r["amount"]) for r in inflows), 2)
    matched = {r["dst"] for r in db.rows(conn, "SELECT dst FROM doc_links WHERE rel = 'from_bank'")}
    diffs = []
    for r in inflows:
        if float(r["amount"]) > 0 and r["entity"] not in matched:
            diffs.append({"kind": "unmatched_receipt", "entity": r["entity"], "amount": float(r["amount"])})
    # Receipts acknowledged as not-for-us (e.g. the sandbox opening probe) are explained, not open.
    explained = {r["entity"] for r in db.rows(conn, "SELECT entity FROM events WHERE kind = 'recon.explained'")}
    diffs = [d for d in diffs if d["entity"] not in explained]
    summary = {"tracked_net": tracked, "increase_balance": increase_balance}
    if increase_balance is not None and abs(increase_balance - tracked) > TOL:
        diffs.append({"kind": "bank_tracking", "increase_balance": increase_balance, "tracked_net": tracked,
                      "diff": round(increase_balance - tracked, 2)})
    return diffs, summary


def run(conn, ctx: dict, use_odoo: bool = True, use_bank: bool = True) -> dict:
    differences: list[dict] = []
    detail: dict = {}
    if use_odoo:
        from connectors import odoo
        tb = odoo.trial_balance(odoo.Odoo())
        differences += books(conn, tb)
        detail["odoo_accounts"] = len(tb)
    if use_bank:
        bal = None
        try:
            from connectors import increase
            bal = increase.balance(increase.Increase())["current"]
        except SystemExit:
            pass  # no bank configured
        d, s = bank(conn, bal)
        differences += d
        detail["bank"] = s
    from ledgerdb import manifest
    differences += [{"kind": "control_code", **c} for c in manifest.changes(conn)]
    ok = not differences
    ev = db.append(conn, kind="recon.result", entity="recon:books",
                   payload={"ok": ok, "differences": differences, **detail}, **ctx)
    return {"ok": ok, "differences": differences, "event": ev, **detail}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="recon", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR", "reconcile-1"))
    r.add_argument("--role", default=os.environ.get("LEDGER_ROLE", "reconcile"))
    r.add_argument("--workflow-version", type=int)
    r.add_argument("--no-odoo", action="store_true")
    r.add_argument("--no-bank", action="store_true")
    args = p.parse_args(argv)
    conn = db.connect(Path(args.db) if args.db else None)
    ctx = {"actor": args.actor, "role": args.role, "workflow": "reconcile", "workflow_version": args.workflow_version}
    try:
        res = run(conn, ctx, not args.no_odoo, not args.no_bank)
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"{type(e).__name__}: {e}"}) + "\n")
        return 2
    sys.stdout.write(json.dumps(res, indent=2, default=str) + "\n")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
