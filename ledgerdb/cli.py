"""Command line for the ledger. Agents use this; they never open the file directly.

    python -m ledgerdb init
    python -m ledgerdb append --actor orders-1 --role orders --kind so.drafted \
        --entity so:SO-1001 --payload @draft.json --source gmail:<id>#att1:p2 \
        --link from_quote=quote:Q-1001
    python -m ledgerdb queue orders
    python -m ledgerdb trace so:SO-1001
    python -m ledgerdb query "SELECT * FROM trial_balance"
    python -m ledgerdb verify

Every command prints JSON. A refused write exits 2 with {"error": ...}.
Defaults for --actor, --role, --workflow, --workflow-version and --run-id can
come from LEDGER_ACTOR, LEDGER_ROLE, LEDGER_WORKFLOW, LEDGER_WORKFLOW_VERSION
and LEDGER_RUN_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import db


def _json_arg(value: str | None, what: str) -> Any:
    if value is None:
        return None
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
    else:
        text = value
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise db.LedgerError(f"{what} is not valid JSON: {e}") from e


def _parse_link(text: str) -> dict[str, Any]:
    # rel=type:id  or  rel=type:id@amount
    if "=" not in text:
        raise db.LedgerError(f"--link must be rel=type:id[@amount], got {text!r}")
    rel, _, rest = text.partition("=")
    to, _, amount = rest.partition("@")
    link: dict[str, Any] = {"rel": rel, "to": to}
    if amount:
        link["amount"] = amount
    return link


def _parse_source(text: str) -> Any:
    return json.loads(text) if text.startswith("{") else text


def _out(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; ledger payloads carry any unicode (→, é, ...).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="ledgerdb", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="path to the database (default: $LEDGER_DB or data/ledger.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database and (re)create views")

    a = sub.add_parser("append", help="append one event")
    a.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
    a.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))
    a.add_argument("--kind", required=True)
    a.add_argument("--entity", required=True)
    a.add_argument("--payload", help="JSON, @file, or - for stdin")
    a.add_argument("--source", action="append", default=[], help="repeatable; string or JSON object")
    a.add_argument("--link", action="append", default=[], help="repeatable; rel=type:id[@amount]")
    a.add_argument("--links", help="JSON list of {rel,to,amount?}, @file, or -")
    a.add_argument("--run-id", default=os.environ.get("LEDGER_RUN_ID"))
    a.add_argument("--workflow", default=os.environ.get("LEDGER_WORKFLOW"))
    a.add_argument("--workflow-version", type=int,
                   default=int(os.environ["LEDGER_WORKFLOW_VERSION"])
                   if os.environ.get("LEDGER_WORKFLOW_VERSION") else None)
    a.add_argument("--on-behalf-of", help="human:<name> for controller decisions")

    q = sub.add_parser("query", help="run a read-only SQL query")
    q.add_argument("sql")
    q.add_argument("params", nargs="*")

    t = sub.add_parser("trace", help="everything linked to an entity, in order")
    t.add_argument("entity")

    s = sub.add_parser("state", help="one entity's events")
    s.add_argument("entity")

    qu = sub.add_parser("queue", help="a driver's open work items")
    qu.add_argument("name", nargs="?")

    sub.add_parser("tb", help="trial balance from our journal")
    sub.add_parser("verify", help="check the hash chain")
    sub.add_parser("feedback", help="overrules and mistakes not yet addressed")

    fo = sub.add_parser("focus", help="declare the item a driver is working on (what its checks look at)")
    fo.add_argument("driver")
    fo.add_argument("entity")
    fo.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
    fo.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))
    fo.add_argument("--workflow-version", type=int)

    n = sub.add_parser("next-id", help="next unused id for a type (reserved atomically when --actor is given)")
    n.add_argument("entity_type")
    n.add_argument("prefix")
    n.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
    n.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))

    args = p.parse_args(argv)
    path = Path(args.db) if args.db else None

    try:
        if args.cmd == "init":
            conn = db.connect(path)
            db.init(conn)
            _out({"ok": True, "db": str(path or db.default_path())})
            return 0

        if args.cmd == "append":
            if not args.actor or not args.role:
                raise db.LedgerError("--actor and --role are required (or LEDGER_ACTOR / LEDGER_ROLE)")
            links = [_parse_link(x) for x in args.link] + (_json_arg(args.links, "--links") or [])
            conn = db.connect(path)
            _out(db.append(
                conn,
                actor=args.actor, role=args.role, kind=args.kind, entity=args.entity,
                payload=_json_arg(args.payload, "--payload"),
                sources=[_parse_source(x) for x in args.source],
                links=links, run_id=args.run_id, workflow=args.workflow,
                workflow_version=args.workflow_version, on_behalf_of=args.on_behalf_of,
            ))
            return 0

        if args.cmd == "focus":
            if not args.actor or not args.role:
                raise db.LedgerError("--actor and --role are required")
            conn = db.connect(path)
            _out(db.append(conn, actor=args.actor, role=args.role, kind="note.focus",
                           entity=f"focus:{args.driver}", payload={"entity": args.entity},
                           workflow=args.driver,
                           workflow_version=args.workflow_version))
            return 0

        if args.cmd == "next-id":
            if not args.actor or not args.role:
                raise db.LedgerError("next-id reserves the id for you: pass --actor and --role")
            conn = db.connect(path)
            _out({"id": db.next_id(conn, args.entity_type, args.prefix, actor=args.actor, role=args.role),
                  "reserved": True})
            return 0

        conn = db.connect(path, readonly=True)
        if args.cmd == "query":
            _out(db.rows(conn, args.sql, args.params))
        elif args.cmd == "trace":
            _out(db.trace(conn, args.entity))
        elif args.cmd == "state":
            _out(db.rows(conn, "SELECT * FROM events WHERE entity = ? ORDER BY seq", [args.entity]))
        elif args.cmd == "queue":
            if args.name:
                _out(db.rows(conn, "SELECT * FROM open_items WHERE queue = ? ORDER BY since_seq", [args.name]))
            else:
                _out(db.rows(conn, "SELECT * FROM open_items ORDER BY queue, since_seq"))
        elif args.cmd == "tb":
            _out(db.rows(conn, "SELECT * FROM trial_balance"))
        elif args.cmd == "verify":
            result = db.verify(conn)
            _out(result)
            return 0 if result["ok"] else 1
        elif args.cmd == "feedback":
            _out(db.rows(conn, "SELECT * FROM feedback_open ORDER BY seq"))
        elif args.cmd == "next-id":
            _out({"id": db.next_id(conn, args.entity_type, args.prefix)})
        return 0
    except (db.LedgerError, FileNotFoundError) as e:
        _out({"error": str(e)})
        return 2
    except Exception as e:  # sqlite errors, bad SQL
        _out({"error": f"{type(e).__name__}: {e}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
