"""The simulated company this desk runs, chosen by DESK_WORLD (default `halyard`).

Every world module in seed/worlds/ defines the same names: COMPANY, SALES_TAX, VENDORS, PRODUCTS,
CUSTOMERS, DESK_ADDR, MSGID_DOMAIN, SCENARIOS_MODULE and FLOW. This module re-exports them, so
callers keep writing `world.VENDORS` whichever company is loaded.
"""

from __future__ import annotations

import importlib
import os

from ledgerdb import db as _db  # noqa: F401  (loads the checkout's .env, which may set DESK_WORLD)

NAME = os.environ.get("DESK_WORLD", "halyard")
_module = importlib.import_module(f"seed.worlds.{NAME}")
globals().update({k: v for k, v in vars(_module).items() if k.isupper()})
