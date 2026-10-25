"""Company policy: thresholds the checks and connectors apply.

Defaults live here. The architect writes structure/policy.json after it has
looked at the books; adopted workflow revisions may change it too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .db import REPO_ROOT


def structure_dir() -> Path:
    """Where this desk's company files live (profile, policy, pipeline). One folder per company:
    `structure/` for the first desk, e.g. `structure/redwood` for another (DESK_STRUCTURE in its .env)."""
    return REPO_ROOT / os.environ.get("DESK_STRUCTURE", "structure")

DEFAULT_POLICY = {
    "margin_floor": 0.15,          # (SO - POs) / SO, untaxed
    "price_tolerance_pct": 0.02,   # vendor bill unit cost vs PO
    "price_tolerance_abs": 0.05,
    "total_tie_abs": 0.01,
    "controller": "human:controller",
    "idle_minutes": 15,            # drivers stop after the whole desk has been quiet this long
}


def policy() -> dict:
    out = dict(DEFAULT_POLICY)
    p = structure_dir() / "policy.json"
    if p.exists():
        out.update(json.loads(p.read_text(encoding="utf-8")))
    return out


def price_tolerance(po_unit_cost: float, pol: dict | None = None) -> float:
    pol = pol or policy()
    return max(pol["price_tolerance_abs"], abs(po_unit_cost) * pol["price_tolerance_pct"])
