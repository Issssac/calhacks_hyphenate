"""Save the workflows' hook tokens for the forwarder. Run it yourself in a terminal:

    python -m desk.save_tokens

It asks for each token (input hidden), checks it against WidgeX's hook server without
starting anything, and writes ~/.revenue-desk/hooks.json. Press Enter to keep an existing token.
"""

from __future__ import annotations

import getpass
import json
import sys

import requests

from desk.dispatch import TOKENS
from workflows.build import HOOK_IDS


def accepted(token: str, port: int = 7477) -> str:
    """Ask the hook server about an event that doesn't exist: a valid token gets 404, a wrong one 401."""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/v1/events/evt_probe00000000",
                         headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except requests.RequestException as e:
        return f"hook server unreachable ({e})"
    return "ok" if r.status_code == 404 else f"refused ({r.status_code})"


def main() -> int:
    current = json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}
    for workflow, hook in HOOK_IDS.items():
        have = " (Enter keeps the saved one)" if hook in current else ""
        token = getpass.getpass(f"Token for revenue-desk-{workflow} ({hook}){have}: ").strip()
        if not token:
            continue
        verdict = accepted(token)
        print(f"  {hook}: {verdict}")
        if verdict == "ok":
            current[hook] = token
    TOKENS.parent.mkdir(parents=True, exist_ok=True)
    TOKENS.write_text(json.dumps(current, indent=1), encoding="utf-8")
    missing = [h for h in HOOK_IDS.values() if h not in current]
    print(f"Saved {len(current)} token(s) to {TOKENS}." + (f" Still missing: {', '.join(missing)}" if missing else ""))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
