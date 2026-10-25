"""Three years of Redwood's past quotes, SIMULATED, for the pricing model to learn from.

    python -m seed.redwood_history [--n 3000] [--seed 7]

Writes <desk home>/pricing/history.csv: one row per past quote, as a distributor's quoting system would keep
it. Each row has the customer, part, quantity, Redwood's cost, the price quoted, and what the salesperson noted
at the time (urgency, willingness to pay, the account's growth). It also says whether the customer took it.

How the rows are made:
- Costs come from the suppliers' price breaks (pricing.sourcing).
- The quoted markups vary the way a sales team's do: around 1.35x, higher on rushes and lower for price-shoppers.
- Each outcome is drawn from the hidden demand model in seed/worlds/redwood_truth.py.

Every row is labelled `source=simulated`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from ledgerdb.db import desk_home
from pricing.sourcing import choose
from seed import world
from seed.worlds import redwood as redwood_world
from seed.worlds import redwood_truth as truth

FIELDS = ["quote_id", "date", "customer", "mpn", "category", "qty", "unit_cost", "unit_price", "markup",
          "urgency", "wtp", "growth", "repeat", "won", "ordered_qty", "source"]


def history_path() -> Path:
    return desk_home() / "pricing" / "history.csv"


def generate(n: int = 3000, seed: int = 7, end: date = date(2026, 10, 24)) -> list[dict]:
    rng = random.Random(seed)
    parts = [p for p in redwood_world.PRODUCTS if p.get("offers")]
    days = 3 * 365
    start = end - timedelta(days=days)
    # Each customer sends quotes as its own stream, at a rate that follows its trajectory, so a growing
    # account really does order more each year and a flat one doesn't (the books can measure it).
    trend = {"growing": 1.45, "flat": 1.0, "shrinking": 0.7}  # quote rate multiplier per year
    def avg_factor(g):
        f = trend[g]
        return 1.0 if f == 1.0 else (f ** 3 - 1) / (3 * math.log(f))
    total_rate = sum(t["volume"] * avg_factor(t["growth"]) for t in truth.CUSTOMERS.values())
    base = n / (days * total_rate)  # quotes per day per unit of volume
    arrivals = []
    for cust, t in truth.CUSTOMERS.items():
        day = 0.0
        while True:
            rate = base * t["volume"] * trend[t["growth"]] ** (day / 365.0)
            day += rng.expovariate(rate)
            if day >= days:
                break
            arrivals.append((day, cust))
    arrivals.sort()
    won_before: set[str] = set()
    rows = []
    for i, (day, cust) in enumerate(arrivals):
        d = start + timedelta(days=int(day))
        t = truth.CUSTOMERS[cust]
        part = rng.choice(parts)
        qty = max(1, int(round(math.exp(rng.gauss(math.log(t["qty"]), 0.9)))))
        src = choose(part["offers"], qty)
        if src["chosen"] is None:
            continue
        cost = src["chosen"]["unit_cost"]
        urgency = "high" if rng.random() < t["urgent"] else ("low" if rng.random() < 0.2 else "normal")
        wtp = rng.choices(["low", "normal", "high"], t["wtp"])[0]
        growth = t["growth"] if rng.random() > 0.1 else rng.choice(["growing", "flat", "shrinking"])
        repeat = cust in won_before
        # How the sales team actually priced it: varied, a little higher on rushes, lower for shoppers.
        # Rushes were priced up hard, and with more spread: a line-down buyer pays what it takes.
        if urgency == "high":
            log_markup = rng.gauss(math.log(1.35) + 0.25, 0.24)
        else:
            log_markup = rng.gauss(math.log(1.35), 0.16) - (0.06 if wtp == "low" else 0)
        markup = min(max(math.exp(log_markup), 1.03), 3.0)
        price = round(cost * markup, 4)
        p = truth.p_win(price / cost, qty, urgency, wtp, growth, repeat, cust)
        won = rng.random() < p
        if won:
            won_before.add(cust)
        rows.append({"quote_id": f"HQ-{i + 1:05d}", "date": d.isoformat(), "customer": cust, "mpn": part["mpn"],
                     "category": part["category"], "qty": qty, "unit_cost": round(cost, 4), "unit_price": price,
                     "markup": round(price / cost, 4), "urgency": urgency, "wtp": wtp, "growth": growth,
                     "repeat": int(repeat), "won": int(won), "ordered_qty": qty if won else 0, "source": "simulated"})
    return rows


def write(rows: list[dict], path: Path | None = None) -> dict:
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "rows": len(rows), "won": sum(r["won"] for r in rows), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="seed.redwood_history", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    if world.NAME != "redwood":
        raise SystemExit("this is Redwood's history: run it with DESK_WORLD=redwood")
    sys.stdout.write(json.dumps(write(generate(args.n, args.seed)), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
