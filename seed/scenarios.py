"""The planted scenarios and answer key for the loaded world (see seed/world.py).

Each world names its scenario module (SCENARIOS_MODULE). This module re-exports SCENARIOS and
BY_ID from it and keeps the helpers that apply to every world.
"""

from __future__ import annotations

import importlib

from . import world

_module = importlib.import_module(world.SCENARIOS_MODULE)
SCENARIOS: list[dict] = _module.SCENARIOS
BY_ID: dict[str, dict] = {s["id"]: s for s in SCENARIOS}


def effective_expect(s: dict, amended: bool) -> dict:
    """The answer key as it applies now: once a quote has been amended, its after-amendment values."""
    exp = dict(s.get("expect", {}))
    if amended:
        for key in ("so", "pos"):
            if f"{key}_after_amendment" in exp:
                exp[key] = exp[f"{key}_after_amendment"]
    return exp
