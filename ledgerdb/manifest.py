"""A fingerprint of the code the controls live in, so a quiet edit to it shows up.

The hard checks, the roles, the ledger and the connectors are ordinary files: an agent
could edit a check to always return True, or add a kind to its own role, without any
proposal. This records a sha256 of each such file in the ledger when a person signs off on
the code. `workflows.build promote` then refuses to run while any of them differs, and
reconciliation and the scorer report the difference.

    python -m ledgerdb.manifest record --actor <who> --role architect --note "reviewed after audit fixes"
    python -m ledgerdb.manifest verify

It catches accidents and casual edits. An agent willing to lie can still edit the file and
re-record the manifest, which is the same trust limit as self-declared identities (README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import db

ROOT = db.REPO_ROOT
CONTROL_FILES = [
    "checks/ledger_checks.py", ".widgex/harnesses/checks/ledger_checks.py",
    "ledgerdb/db.py", "ledgerdb/cli.py", "ledgerdb/policy.py", "ledgerdb/manifest.py", "ledgerdb/schema.sql",
    "ledgerdb/views.sql", "ledgerdb/roles.toml",
    "connectors/odoo.py", "connectors/increase.py", "connectors/inbox.py", "connectors/gmail.py",
    "workflows/build.py", "recon.py",
    # Pricing: how a recommendation is computed. The agent supplies judgement calls; it can't change these.
    "pricing/sourcing.py", "pricing/features.py", "pricing/model.py",
]


def fingerprint() -> dict[str, str | None]:
    out = {}
    for rel in CONTROL_FILES:
        p = ROOT / rel
        out[rel] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p.exists() else None
    return out


def record(conn, actor: str, role: str, note: str = "") -> dict:
    return db.append(conn, actor=actor, role=role, kind="structure.manifest", entity="structure:manifest",
                     payload={"files": fingerprint(), "note": note})


def changes(conn) -> list[dict]:
    """Control files that differ from the last recorded manifest (empty if none recorded yet: see `missing`)."""
    row = conn.execute("SELECT payload FROM events WHERE entity = 'structure:manifest' AND kind = 'structure.manifest'"
                       " ORDER BY seq DESC LIMIT 1").fetchone()
    if not row:
        return [{"file": "*", "problem": "no manifest recorded"}]
    recorded = json.loads(row[0])["files"]
    now = fingerprint()
    return [{"file": f, "problem": "changed since the manifest was recorded"}
            for f in sorted(set(recorded) | set(now)) if recorded.get(f) != now.get(f)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ledgerdb.manifest", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--actor", required=True)
    r.add_argument("--role", required=True)
    r.add_argument("--note", default="")
    sub.add_parser("verify")
    args = p.parse_args(argv)
    conn = db.connect()
    if args.cmd == "record":
        out = record(conn, args.actor, args.role, args.note)
    else:
        out = {"ok": not changes(conn), "changes": changes(conn)}
    sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return 0 if args.cmd == "record" or out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
