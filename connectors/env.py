"""Tiny .env loader and token store, so we need no extra dependency."""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


def load_env(path: Path | None = None) -> None:
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def need(*keys: str) -> dict[str, str]:
    load_env()
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing settings in .env: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


def read_token(name: str) -> dict:
    p = DATA_DIR / f"{name}.token.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_token(name: str, token: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{name}.token.json").write_text(json.dumps(token, indent=2), encoding="utf-8")
