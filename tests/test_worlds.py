"""Every world loads with the same names, and Redwood's supplier data is consistent."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("COMPANY", "SALES_TAX", "VENDORS", "PRODUCTS", "CUSTOMERS", "DESK_ADDR", "MSGID_DOMAIN",
         "SCENARIOS_MODULE", "FLOW")


def test_every_world_defines_the_same_names():
    for name in ("halyard", "redwood"):
        mod = importlib.import_module(f"seed.worlds.{name}")
        missing = [n for n in NAMES if not hasattr(mod, n)]
        assert not missing, (name, missing)
        importlib.import_module(mod.SCENARIOS_MODULE)


def test_loader_follows_desk_world(tmp_path):
    env = {**os.environ, "DESK_WORLD": "redwood", "REVENUE_DESK_HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", "from seed import world; import json; "
                          "print(json.dumps([world.NAME, world.COMPANY['name'], world.FLOW]))"],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["redwood", "Redwood Micro Components", "rfq"]


def test_redwood_offers_are_consistent():
    w = importlib.import_module("seed.worlds.redwood")
    vendors = {v["name"]: v for v in w.VENDORS}
    for p in w.PRODUCTS:
        assert p["offers"], p["name"]
        for off in p["offers"]:
            assert off["vendor"] in vendors
            qtys = [q for q, _ in off["breaks"]]
            prices = [c for _, c in off["breaks"]]
            assert qtys == sorted(qtys), (p["name"], off["vendor"])
            assert prices == sorted(prices, reverse=True), (p["name"], off["vendor"], "bulk must not cost more")
        if p["mpn"] == "MC68HC908GP32CFBE":
            assert all(not o["authorized"] for o in p["offers"]), "the end-of-life part is broker-only"
        else:
            assert any(o["authorized"] for o in p["offers"]), p["name"]
