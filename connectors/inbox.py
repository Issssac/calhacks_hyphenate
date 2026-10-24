"""One door for incoming mail: Gmail when it is connected, plus a local drop folder.

    python -m connectors.inbox pull --actor intake-agent --role intake --workflow intake

The drop folder (~/.revenue-desk/inbox/drop/*.eml) is how simulated customers and suppliers
deliver mail when no Gmail account is connected, and how a judge can hand the desk a scenario
of their own: save an email as .eml and drop it in. Both paths save the message the same way
and append the same mail.received event; only the source differs (gmail:<id> vs mailbox:<file>).
"""

from __future__ import annotations

import argparse
import email
import email.policy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from ledgerdb import db
from . import gmail
from .env import DATA_DIR, load_env, read_token

DROP = DATA_DIR / "inbox" / "drop"


def _thread_id(msg) -> str:
    refs = str(msg.get("References", "")).split()
    root = refs[0] if refs else str(msg.get("In-Reply-To", "")).strip() or str(msg.get("Message-ID", "")).strip()
    return "T" + hashlib.sha1(root.encode()).hexdigest()[:12] if root else ""


def pull_drop(conn, ctx: dict, drop: Path = DROP) -> list[dict]:
    drop.mkdir(parents=True, exist_ok=True)
    done = drop / "processed"
    done.mkdir(exist_ok=True)
    new = []
    for path in sorted(drop.glob("*.eml")):
        raw = path.read_bytes()
        msg_id = "L" + hashlib.sha256(raw).hexdigest()[:16]
        entity = f"mail:M-{msg_id}"
        if not conn.execute("SELECT 1 FROM events WHERE entity = ? LIMIT 1", (entity,)).fetchone():
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            payload = gmail.save_message(msg_id, raw, {"threadId": _thread_id(msg), "labelIds": ["drop"]})
            payload["delivered_via"] = "drop folder"
            db.append(conn, kind="mail.received", entity=entity, payload=payload,
                      sources=[f"mailbox:{path.name}"] + [f"mailbox:{path.name}#att{a['n']}"
                                                          for a in payload["attachments"]], **ctx)
            new.append({"entity": entity, "subject": payload["subject"], "from": payload["from"],
                        "attachments": [a["name"] for a in payload["attachments"]], "via": "drop"})
        shutil.move(str(path), str(done / path.name))
    return new


def pull(conn, ctx: dict) -> dict:
    load_env()
    out: dict = {"new": []}
    if os.environ.get("GMAIL_CLIENT_ID") and read_token("gmail-read"):
        try:
            out["new"] += [{**m, "via": "gmail"} for m in gmail.pull(conn, gmail.Gmail("read"), ctx)["new"]]
        except gmail.GmailError as e:
            out["gmail_error"] = str(e)
    else:
        out["gmail"] = "not connected; drop folder only"
    out["new"] += pull_drop(conn, ctx)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="connectors.inbox", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("pull")
    pl.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
    pl.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))
    pl.add_argument("--workflow", default=os.environ.get("LEDGER_WORKFLOW"))
    pl.add_argument("--workflow-version", type=int)
    sub.add_parser("where")
    args = p.parse_args(argv)
    if args.cmd == "where":
        sys.stdout.write(json.dumps({"drop_folder": str(DROP)}) + "\n")
        return 0
    if not args.actor or not args.role:
        sys.stdout.write(json.dumps({"error": "--actor and --role are required"}) + "\n")
        return 2
    conn = db.connect(Path(args.db) if args.db else None)
    ctx = {"actor": args.actor, "role": args.role, "workflow": args.workflow,
           "workflow_version": args.workflow_version}
    try:
        sys.stdout.write(json.dumps(pull(conn, ctx), indent=2) + "\n")
    except db.LedgerError as e:
        sys.stdout.write(json.dumps({"error": str(e)}) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
