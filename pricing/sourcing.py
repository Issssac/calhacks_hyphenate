"""Find a supplier for a part at a quantity, and the cost curve across quantity tiers.

An offer is one supplier's terms for one part:
    {"vendor", "authorized": bool, "lead_days": int, "stock": int, "breaks": [(min_qty, unit_cost), ...]}

Prices and lead times come from Odoo's product.supplierinfo (what the company has on file). Stock comes from
the suppliers' availability feeds; in this simulation that feed is the world file (seed/worlds/<world>.py),
the same numbers a distributor's buyer would see on a supplier portal.

The rule, in code so it is the same for every quote:
  * only authorized sources, unless the customer allows brokers or nothing authorized can supply;
  * the supplier must hold enough stock and meet the need-by date (lead time);
  * the cheapest unit cost at that quantity wins; ties go to the shorter lead time.
A broker pick is always flagged for review: it is not an authorized source.
"""

from __future__ import annotations

from typing import Any

TIERS = (1, 10, 100, 500, 1000, 5000, 10000)


def unit_cost(offer: dict, qty: float) -> float | None:
    """The supplier's unit cost at this quantity: the price of the largest break at or below it."""
    cost = None
    for min_qty, price in sorted(offer["breaks"]):
        if qty >= min_qty:
            cost = price
    return cost


def choose(offers: list[dict], qty: float, days_available: int | None = None,
           allow_broker: bool = False) -> dict[str, Any]:
    """Pick the supplier for `qty` units. Returns the choice, the alternatives considered, and why."""
    considered = []
    for off in offers:
        c = unit_cost(off, qty)
        why_not = []
        if c is None:
            why_not.append(f"minimum order {min(q for q, _ in off['breaks'])}")
        if off.get("stock") is not None and off["stock"] < qty:
            why_not.append(f"only {off['stock']} in stock")
        if days_available is not None and off.get("lead_days", 0) > days_available:
            why_not.append(f"lead time {off['lead_days']}d > {days_available}d available")
        considered.append({"vendor": off["vendor"], "authorized": off.get("authorized", True),
                           "unit_cost": c, "lead_days": off.get("lead_days"), "stock": off.get("stock"),
                           "excluded": "; ".join(why_not) or None})
    ok = [x for x in considered if not x["excluded"]]
    authorized = [x for x in ok if x["authorized"]]
    flags = []
    if allow_broker:
        pool = ok
    elif authorized:
        pool = authorized
    else:
        # Nothing authorized can supply. A broker can, but that needs a person's say-so.
        pool = ok
        if ok:
            flags.append("broker: no authorized source can supply this; needs approval (counterfeit risk)")
    if not pool:
        return {"chosen": None, "considered": considered, "flags": ["no source can supply this quantity in time"]}
    best = min(pool, key=lambda x: (x["unit_cost"], x["lead_days"] or 0))
    if not best["authorized"] and not flags:
        flags.append("broker: not an authorized source; needs approval (counterfeit risk)")
    return {"chosen": best, "considered": considered, "flags": flags}


def cost_curve(offers: list[dict], tiers: tuple[int, ...] = TIERS, days_available: int | None = None,
               allow_broker: bool = False) -> list[dict]:
    """The best source and unit cost at each quantity tier (None where nothing can supply it)."""
    out = []
    for q in tiers:
        c = choose(offers, q, days_available, allow_broker)
        if c["chosen"] is None and days_available is not None:
            # Nobody can make the date. Quote the earliest delivery that can be made, and say it's late.
            late = choose(offers, q, None, allow_broker)
            if late["chosen"] is not None:
                c = {**late, "flags": late["flags"] + [
                    f"can't meet the need-by date: earliest delivery {late['chosen']['lead_days']} days "
                    f"({days_available} available)"]}
        ch = c["chosen"]
        out.append({"qty": q, "unit_cost": ch["unit_cost"] if ch else None, "vendor": ch["vendor"] if ch else None,
                    "authorized": ch["authorized"] if ch else None, "lead_days": ch["lead_days"] if ch else None,
                    "flags": c["flags"]})
    return out


# ---------------------------------------------------------------- where offers come from

def part_category(mpn: str) -> str | None:
    """The catalogue category of a part (MCU, MOSFET, FPGA, ...), or None if it isn't in the catalogue."""
    from seed import world
    for p in world.PRODUCTS:
        if p.get("mpn") == mpn or p["name"] == mpn:
            return p.get("category")
    return None


def offers_from_world(mpn: str) -> list[dict]:
    """The simulated supplier feeds: prices, lead times and stock for one part."""
    from seed import world
    for p in world.PRODUCTS:
        if p.get("mpn") == mpn or p["name"] == mpn:
            return [dict(o) for o in p.get("offers") or []]
    return []


def offers_from_odoo(o, mpn: str) -> list[dict]:
    """Prices and lead times as Odoo has them on file, with stock from the suppliers' availability feeds."""
    rows = o.search_read("product.supplierinfo",
                         ["|", ("product_tmpl_id.default_code", "=", mpn), ("product_tmpl_id.name", "=", mpn)],
                         ["partner_id", "min_qty", "price", "delay"])
    feed = {x["vendor"]: x for x in offers_from_world(mpn)}
    by_vendor: dict[str, dict] = {}
    for r in rows:
        name = r["partner_id"][1]
        off = by_vendor.setdefault(name, {"vendor": name, "lead_days": int(r["delay"] or 0), "breaks": [],
                                          "authorized": feed.get(name, {}).get("authorized", True),
                                          "stock": feed.get(name, {}).get("stock")})
        off["breaks"].append((float(r["min_qty"]), float(r["price"])))
    for off in by_vendor.values():
        off["breaks"].sort()
    return list(by_vendor.values())


def main(argv: list[str] | None = None) -> int:
    """python -m pricing.sourcing choose --mpn X --qty N [--days D] [--allow-broker]
       python -m pricing.sourcing curve  --mpn X [--qty N] [--days D]"""
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(prog="pricing.sourcing", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("choose", "curve"):
        sp = sub.add_parser(name)
        sp.add_argument("--mpn", required=True)
        sp.add_argument("--qty", type=float, default=1)
        sp.add_argument("--days", type=int, default=None, help="days until the need-by date")
        sp.add_argument("--allow-broker", action="store_true")
    args = ap.parse_args(argv)
    from connectors.odoo import Odoo
    offers = offers_from_odoo(Odoo(), args.mpn)
    if not offers:
        print(json.dumps({"mpn": args.mpn, "problem": "no supplier prices on file in Odoo for this part"}))
        return 1
    if args.cmd == "choose":
        out = choose(offers, args.qty, args.days, args.allow_broker)
        if out["chosen"] is None and args.days is not None:
            late = choose(offers, args.qty, None, args.allow_broker)
            if late["chosen"]:
                late["flags"].append(f"can't meet the need-by date: earliest {late['chosen']['lead_days']} days")
                out = late
    else:
        out = cost_curve(offers, tuple(sorted(set(TIERS) | {int(args.qty)})), args.days, args.allow_broker)
    sys.stdout.write(json.dumps({"mpn": args.mpn, "qty": args.qty, "result": out}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
