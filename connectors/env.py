"""Tiny .env loader and token store, so we need no extra dependency.

Settings are read from <repo>/.env and then ~/.revenue-desk/.env (the shared
desk home), so an agent in any worktree finds the same keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ledgerdb.db import desk_home

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = desk_home()


def load_env(path: Path | None = None) -> None:
    for p in ([path] if path else [REPO_ROOT / ".env", desk_home() / ".env"]):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def need(*keys: str) -> dict[str, str]:
    load_env()
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing settings in .env: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


def read_token(name: str) -> dict:
    p = DATA_DIR / "tokens" / f"{name}.token.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_token(name: str, token: dict) -> None:
    d = DATA_DIR / "tokens"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.token.json").write_text(json.dumps(token, indent=2), encoding="utf-8")
