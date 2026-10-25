"""How the forwarder wakes a workflow. Pick one with --dispatcher or pipeline.json "dispatcher".

    dry-run      print only
    file-queue   write a wake request to ~/.revenue-desk/dispatch/<workflow>-<ts>.json; a session that has
                 the WidgeX api serves it (desk/serve.py, run inside a WidgeX terminal)
    widgex       call WidgeX's harness trigger directly (filled in once its interface is confirmed)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ledgerdb.db import REPO_ROOT, desk_home

QUEUE = desk_home() / "dispatch"


class DryRun:
    def wake(self, workflow: str, text: str, keys: list[str] | None = None) -> dict:
        print(f"[dry-run] would wake {workflow}:\n{text}\n")
        return {"ok": False, "via": "dry-run"}


class FileQueue:
    def wake(self, workflow: str, text: str, keys: list[str] | None = None) -> dict:
        QUEUE.mkdir(parents=True, exist_ok=True)
        path = QUEUE / f"{workflow}-{int(time.time() * 1000)}.json"
        path.write_text(json.dumps({"workflow": workflow, "message": text, "queued_at": time.time()}), encoding="utf-8")
        return {"ok": True, "via": "file-queue", "detail": path.name}


TOKENS = desk_home() / "hooks.json"   # {"hook-rdorders": "whk_…", …}: written by a person, never printed


class WidgeXHooks:
    """Start a workflow run by posting to its WidgeX hook (http://127.0.0.1:7477/v1/hooks/<id>).

    The hook id is fixed per workflow (workflows/build.py HOOK_IDS). Its token is made once, by a
    person, on the workflow's config page in WidgeX, and saved in ~/.revenue-desk/hooks.json.
    WidgeX queues an event that arrives during a run, and holds it behind a review gate.
    """

    def __init__(self, port: int | None = None) -> None:
        import os
        self.port = port or int(os.environ.get("WIDGEX_HOOK_PORT", "7477"))
        self.backoff_until: dict[str, float] = {}
        self.failures: dict[str, int] = {}

    def _token(self, hook_id: str) -> str | None:
        if not TOKENS.exists():
            return None
        return json.loads(TOKENS.read_text(encoding="utf-8")).get(hook_id)

    def wake(self, workflow: str, text: str, keys: list[str] | None = None) -> dict:
        import hashlib
        import requests
        from workflows.build import HOOK_IDS

        if time.time() < self.backoff_until.get(workflow, 0):
            return {"ok": False, "via": "widgex-hook", "detail": "backing off after a failure"}
        hook = HOOK_IDS.get(workflow)
        token = self._token(hook) if hook else None
        if not token:
            return {"ok": False, "via": "widgex-hook", "detail": f"no token for {hook} in {TOKENS}"}
        idem = hashlib.sha256(("|".join(sorted(keys or [])) or text).encode()).hexdigest()[:32]
        try:
            r = requests.post(f"http://127.0.0.1:{self.port}/v1/hooks/{hook}", timeout=10,
                              headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem},
                              json={"workflow": workflow, "reasons": keys or [], "message": text})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text[:200]}
        except requests.RequestException as e:
            r, body = None, {"error": str(e)[:200]}
        ok = r is not None and r.status_code == 202
        if ok:
            self.failures.pop(workflow, None)
        else:
            n = self.failures[workflow] = self.failures.get(workflow, 0) + 1
            self.backoff_until[workflow] = time.time() + min(300, 15 * 2 ** n)
        return {"ok": ok, "via": "widgex-hook", "status": r.status_code if r is not None else None,
                "detail": {k: body.get(k) for k in ("eventId", "status", "sessionId", "error", "reason", "text") if k in body}}


def make_dispatcher(name: str | None):
    if not name:
        pipeline = REPO_ROOT / "structure" / "pipeline.json"
        name = json.loads(pipeline.read_text(encoding="utf-8")).get("dispatcher", "file-queue") if pipeline.exists() else "file-queue"
    return {"dry-run": DryRun, "file-queue": FileQueue, "widgex": WidgeXHooks, "widgex-hooks": WidgeXHooks}[name]()
