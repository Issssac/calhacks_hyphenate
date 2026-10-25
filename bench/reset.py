"""Start a benchmark run from scratch. Nothing is deleted: the previous run is archived.

    python -m bench.reset

1. Moves the ledger, the simulator's state and all pulled mail to ~/.revenue-desk/runs/<timestamp>/.
2. Keeps workflows/history/ and the live workflows; the new ledger is told each workflow's version.
3. Recreates the Odoo database `revenue` from empty and seeds Halyard Marine Supply.
4. Creates a fresh ledger.

The Increase sandbox keeps its history (sandbox money can't be un-sent). Reconcile treats
earlier deposits as the opening position.

After this, compile and validate the workflows, promote them, send the quotes
(`python -m seed.sim quotes --phase 1`), and start the agents.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from connectors.env import DATA_DIR
from ledgerdb import db

ROOT = Path(__file__).resolve().parent.parent
MODULES = "sale_management,purchase,stock,account"


def sh(*args: str, timeout: int = 1200) -> str:
    r = subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r.stdout


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    arch = DATA_DIR / "runs" / stamp
    arch.mkdir(parents=True)
    moved = []
    # Workflow versions carry over: the live files and workflows/history stay where they are, and the
    # new ledger is told which version each workflow is at, so numbering continues instead of restarting.
    carried = {}
    if db.default_path().exists():
        old = db.connect(db.default_path())
        try:
            carried = {r["entity"]: r for r in db.rows(old, "SELECT entity, version, sha256 FROM workflow_current")}
        except Exception:
            carried = {}
        old.close()
    for name in ("ledger.db", "ledger.db-wal", "ledger.db-shm", "sim_state.json", "inbox", "outbox"):
        p = DATA_DIR / name
        if p.exists():
            shutil.move(str(p), str(arch / name))
            moved.append(name)
    for p in (ROOT / "workflows" / "proposed").glob("*.json") if (ROOT / "workflows" / "proposed").exists() else []:
        p.unlink()

    sh("docker", "compose", "stop", "odoo")
    sh("docker", "compose", "exec", "-T", "db", "dropdb", "-U", "odoo", "--if-exists", "--force", "revenue")
    sh("docker", "compose", "run", "--rm", "odoo", "odoo", "-d", "revenue", "-i", MODULES, "--stop-after-init",
       "--without-demo=all")
    sh("docker", "compose", "up", "-d", "odoo")
    time.sleep(8)
    seeded = sh(sys.executable, "-m", "seed.company")

    conn = db.connect()
    db.init(conn)
    for entity, r in carried.items():
        db.append(conn, actor="bench-reset", role="architect", kind="workflow.adopted", entity=entity,
                  payload={"version": r["version"], "sha256": r["sha256"], "carried_over": True,
                           "summary": f"carried over from the run archived at {arch}"})
    print(json.dumps({"archived_to": str(arch), "moved": moved, "carried_versions": {k: v["version"] for k, v in carried.items()},
                      "odoo": "recreated and seeded",
                      "seed": json.loads(seeded), "ledger": str(db.default_path())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
