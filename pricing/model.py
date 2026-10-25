"""The win-probability model and the price-break schedule it recommends.

    python -m pricing.model fit       --actor pricing-agent --role pricing      refit on the history + new outcomes
    python -m pricing.model recommend --rfq quote:Q-1001 --actor pricing-agent --role pricing
    python -m pricing.model explain                                               coefficients, in plain words

The model: P(win) = logistic(x . beta), where x is `features.row(markup, qty, urgency, wtp, growth, repeat)` and
markup is unit price / Redwood's unit cost. It is fit by Newton's method (IRLS) with a small ridge penalty on the
quote history (<desk home>/pricing/history.csv) plus every outcome the desk has recorded since. Bootstrap refits
give the uncertainty band.

The schedule: for each quantity tier q with cost c(q) (pricing.sourcing), choose the unit price p maximizing

    E[profit] = P(win | p, q, calls) * q * (p - c(q))  -  quote handling cost

then make it a real price-break curve: never higher at a larger quantity, and never under the margin floor. The
agent supplies the three judgement calls. It does not choose the price, and the payload records what came from
where.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ledgerdb import db
from ledgerdb.policy import policy
from . import features as F
from .sourcing import TIERS, cost_curve, part_category

VERSION = "pricing-model/1"
CAP_MARKUP = 3.0
GRID = 400


def home() -> Path:
    return db.desk_home() / "pricing"


def history_path() -> Path:
    return home() / "history.csv"


def model_path() -> Path:
    return home() / "model.json"


# ---------------------------------------------------------------- fitting

def design(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    X = np.array([F.row(float(r["markup"]), float(r["qty"]), r["urgency"], r["wtp"], r["growth"],
                        bool(int(r["repeat"]))) for r in rows])
    y = np.array([float(int(r["won"])) for r in rows])
    return X, y


def irls(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, iters: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Ridge-penalized logistic regression by Newton's method. Returns (beta, covariance)."""
    k = X.shape[1]
    beta = np.zeros(k)
    pen = l2 * np.eye(k)
    pen[0, 0] = 0.0  # the intercept is not shrunk
    for _ in range(iters):
        z = X @ beta
        p = 1.0 / (1.0 + np.exp(-z))
        w = p * (1 - p)
        H = X.T @ (X * w[:, None]) + pen
        g = X.T @ (y - p) - pen @ beta
        step = np.linalg.solve(H, g)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    z = X @ beta
    p = 1.0 / (1.0 + np.exp(-z))
    H = X.T @ (X * (p * (1 - p))[:, None]) + pen
    return beta, np.linalg.inv(H)


def _auc(y: np.ndarray, s: np.ndarray) -> float:
    order = np.argsort(s)
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def fit_rows(rows: list[dict], data_sha256: str, bootstrap: int = 120, seed: int = 11) -> dict[str, Any]:
    X, y = design(rows)
    beta, cov = irls(X, y)
    p = 1.0 / (1.0 + np.exp(-(X @ beta)))
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(bootstrap):
        idx = rng.integers(0, len(y), len(y))
        boots.append(irls(X[idx], y[idx], iters=40)[0].tolist())
    # How far the history reaches. The optimizer never prices beyond what was actually quoted in a similar
    # situation (97.5th percentile of markups), because past that the curve is extrapolation, not evidence.
    support = {}
    for key, sel in (("all", lambda r: True),
                     ("urgency_high", lambda r: r["urgency"] == "high"),
                     ("wtp_low", lambda r: r["wtp"] == "low")):
        mk = [float(r["markup"]) for r in rows if sel(r)]
        if len(mk) >= 30:
            support[key] = round(float(np.quantile(mk, 0.975)), 4)
    return {
        "version": VERSION, "features": list(F.FEATURES),
        "coef": [round(float(b), 5) for b in beta], "se": [round(float(s), 5) for s in np.sqrt(np.diag(cov))],
        "bootstrap": [[round(v, 5) for v in b] for b in boots],
        "n": int(len(y)), "wins": int(y.sum()), "support": support,
        "metrics": {"log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
                    "brier": round(float(np.mean((p - y) ** 2)), 4), "auc": round(_auc(y, p), 4)},
        "data_sha256": data_sha256,
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- prediction and the schedule

def p_win(beta, markup: float, qty: float, calls: dict, repeat: bool) -> float:
    x = np.array(F.row(markup, qty, calls["urgency"], calls["wtp"], calls["growth"], repeat))
    return float(1.0 / (1.0 + math.exp(-float(x @ np.asarray(beta)))))


def markup_cap(support: dict | None, calls: dict) -> float:
    """The highest markup the history supports for this situation (see fit_rows)."""
    if not support:
        return CAP_MARKUP
    key = "urgency_high" if calls["urgency"] == "high" else ("wtp_low" if calls["wtp"] == "low" else "all")
    return min(CAP_MARKUP, support.get(key, support.get("all", CAP_MARKUP)))


def best_markup(beta, qty: float, calls: dict, repeat: bool, floor: float, cap: float = CAP_MARKUP) -> float:
    """The markup in [1 + floor, cap] maximizing P(win) * (markup - 1). (Cost and qty scale out.)"""
    cap = max(cap, 1.0 + floor + 1e-6)
    grid = np.exp(np.linspace(math.log(1.0 + floor), math.log(cap), GRID))
    x = np.array([F.row(m, qty, calls["urgency"], calls["wtp"], calls["growth"], repeat) for m in grid])
    p = 1.0 / (1.0 + np.exp(-(x @ np.asarray(beta))))
    return float(grid[int(np.argmax(p * (grid - 1.0)))])


def schedule(beta, curve: list[dict], calls: dict, repeat: bool, floor: float, quote_cost: float,
             band_from: list | None = None, support: dict | None = None) -> list[dict]:
    """One row per tier: price, markup, margin, win probability, expected profit, source, and any flags.

    `floor` is the minimum MARKUP over cost (price >= cost * (1 + floor)). For a margin floor m, pass
    1 / (1 - m) - 1."""
    out = []
    prev_price = None
    cap = markup_cap(support, calls)
    for t in curve:
        q, c = t["qty"], t["unit_cost"]
        if c is None:
            out.append({"qty": q, "unit_price": None, "unit_cost": None, "vendor": None,
                        "flags": t["flags"] or ["no source"]})
            continue
        m = best_markup(beta, q, calls, repeat, floor, cap)
        price = c * m
        flags = list(t["flags"])
        if m >= cap * 0.999:
            flags.append(f"capped at {cap:.2f}x cost, the highest markup our history has for a quote like this")
        if prev_price is not None and price > prev_price:
            price = prev_price  # a bulk curve never charges more per unit for buying more
        if price < c * (1 + floor):
            price = c * (1 + floor)
            if prev_price is not None and price > prev_price:
                flags.append("price rises at this quantity: the only source that can supply it costs more")
        step = 1e-4 if price < 1 else 1e-3
        price = round(price, 4 if price < 1 else 3)
        if price < c * (1 + floor) - 1e-12:
            price = round(price + step, 4 if price < 1 else 3)  # rounding must never cross the floor
        pw = p_win(beta, price / c, q, calls, repeat)
        row = {"qty": q, "unit_price": price, "unit_cost": c, "markup": round(price / c, 4),
               "margin": round(1 - c / price, 4), "p_win": round(pw, 4),
               "expected_profit": round(pw * q * (price - c) - quote_cost, 2),
               "vendor": t["vendor"], "authorized": t["authorized"], "lead_days": t["lead_days"], "flags": flags}
        if band_from:
            opts = sorted(c * best_markup(b, q, calls, repeat, floor, cap) for b in band_from)
            row["band"] = [round(opts[int(0.1 * (len(opts) - 1))], 4), round(opts[int(0.9 * (len(opts) - 1))], 4)]
        out.append(row)
        prev_price = price
    return out


def tiers_for(qty: int) -> tuple[int, ...]:
    return tuple(sorted(set(TIERS) | {int(qty)}))


# ---------------------------------------------------------------- ledger glue

def _ctx(args) -> dict:
    return {"actor": args.actor, "role": args.role, "workflow": "pricing"}


def history_rows(conn) -> tuple[list[dict], str]:
    """The quote history plus every outcome recorded since, and a hash of exactly that data."""
    import csv
    rows = []
    if history_path().exists():
        with history_path().open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    for e in db.rows(conn, "SELECT payload FROM events WHERE kind = 'pricing.outcome' ORDER BY seq"):
        rows.extend(e["payload"].get("rows", []))
    blob = json.dumps(rows, sort_keys=True).encode()
    return rows, hashlib.sha256(blob).hexdigest()


def fit_and_record(conn, ctx: dict) -> dict:
    rows, sha = history_rows(conn)
    if not rows:
        raise SystemExit(f"no quote history at {history_path()}")
    m = fit_rows(rows, sha)
    home().mkdir(parents=True, exist_ok=True)
    model_path().write_text(json.dumps(m), encoding="utf-8")
    n = conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'pricing.model_fitted'").fetchone()[0]
    summary = {k: v for k, v in m.items() if k != "bootstrap"}
    summary["model_version"] = n + 1
    summary["model_sha256"] = hashlib.sha256(model_path().read_bytes()).hexdigest()
    db.append(conn, kind="pricing.model_fitted", entity="pricing:model", payload=summary,
              sources=[f"file:{history_path().name}#sha256={sha[:16]}"], **ctx)
    return summary


def load_model(conn, ctx: dict) -> dict:
    if not model_path().exists():
        fit_and_record(conn, ctx)
    m = json.loads(model_path().read_text(encoding="utf-8"))
    m["model_sha256"] = hashlib.sha256(model_path().read_bytes()).hexdigest()
    return m


def _days_until(need_by: str | None) -> int | None:
    if not need_by:
        return None
    try:
        return max(0, (date.fromisoformat(str(need_by)[:10]) - date.today()).days)
    except ValueError:
        return None


def recommend(conn, rfq: str, ctx: dict, odoo=None) -> dict:
    """Price every line of an RFQ from its latest extraction and the latest judgement calls; record it."""
    quote = db.rows(conn, "SELECT seq, payload FROM events WHERE entity = ? AND kind IN "
                          "('quote.extracted','quote.clarified','quote.amended') ORDER BY seq DESC LIMIT 1", [rfq])
    if not quote:
        raise SystemExit(f"{rfq} has no extracted lines yet")
    assess = db.rows(conn, "SELECT seq, payload FROM events WHERE entity = ? AND kind = 'pricing.assessment' "
                           "ORDER BY seq DESC LIMIT 1", [rfq])
    if not assess:
        raise SystemExit(f"{rfq} has no pricing.assessment: record the judgement calls first")
    problems = F.assessment_problems(assess[0]["payload"])
    if problems:
        raise SystemExit("the assessment can't be used: " + "; ".join(problems))
    calls = F.values(assess[0]["payload"])
    q = quote[0]["payload"]
    customer = q.get("customer")
    model = load_model(conn, ctx)
    pol = policy()
    floor = float(pol.get("pricing", {}).get("margin_floor", pol.get("margin_floor", 0.15)))
    quote_cost = float(pol.get("pricing", {}).get("quote_cost", 15.0))
    contracts = pol.get("contract_prices", {}).get(customer, {})
    repeat = F.is_repeat(customer, history_path())
    measured = F.measured_growth(customer, history_path(), conn)
    if odoo is None:
        from connectors.odoo import Odoo
        odoo = Odoo()
    from .sourcing import offers_from_odoo
    lines = []
    for ln in q.get("lines", []):
        mpn = ln.get("mpn") or ln.get("item")
        qty = int(float(ln.get("qty") or 1))
        days = _days_until(ln.get("need_by") or q.get("need_by"))
        offers = offers_from_odoo(odoo, mpn)
        curve = cost_curve(offers, tiers_for(qty), days)
        category = part_category(mpn)
        # Floors finance has set (adopted policy keys, learned from their corrections) only ever raise the floor.
        line_floor = max(floor, float(pol.get("margin_floor_by_category", {}).get(category, 0)),
                         float(pol.get("margin_floor_by_customer", {}).get(customer, 0)))
        floor_markup = 1 / (1 - line_floor) - 1  # a margin floor m means price >= cost / (1 - m)
        entry = {"mpn": mpn, "qty": qty, "category": category, "margin_floor": line_floor,
                 "need_by": ln.get("need_by") or q.get("need_by"), "days_available": days}
        if mpn in contracts:
            entry["contract_price"] = contracts[mpn]
            entry["schedule"] = [{"qty": t["qty"], "unit_price": contracts[mpn], "unit_cost": t["unit_cost"],
                                  "vendor": t["vendor"], "flags": ["contract price: not modelled"]} for t in curve]
        else:
            entry["schedule"] = schedule(model["coef"], curve, calls, repeat, floor_markup, quote_cost,
                                         model["bootstrap"], model.get("support"))
            entry["neutral"] = schedule(model["coef"], curve, F.NEUTRAL, repeat, floor_markup, quote_cost,
                                        support=model.get("support"))
        entry["requested"] = next((r for r in entry["schedule"] if r["qty"] == qty), None)
        lines.append(entry)
    inputs = {"rfq": rfq, "quote_seq": quote[0]["seq"], "assessment_seq": assess[0]["seq"], "calls": calls,
              "model_sha256": model["model_sha256"], "lines": [(x["mpn"], x["qty"]) for x in lines]}
    inputs_sha = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    total = sum(((x["requested"] or {}).get("unit_price") or 0) * x["qty"] for x in lines)
    unsourced = [x["mpn"] for x in lines if not (x["requested"] or {}).get("unit_price")]
    evidence = {k: assess[0]["payload"]["calls"][k].get("evidence", []) for k in F.CALLS}
    payload = {"rfq": rfq, "customer": customer, "calls": calls, "evidence": evidence,
               "measured_growth": measured, "repeat": repeat,
               "growth_disagrees": calls["growth"] != measured["label"],
               "margin_floor": floor, "quote_cost": quote_cost, "lines": lines,
               "requested_total": round(total, 2), "unsourced": unsourced,
               "inputs": inputs, "inputs_sha256": inputs_sha,
               "model": {"version": model["version"], "sha256": model["model_sha256"], "n": model["n"],
                         "metrics": model["metrics"]}}
    rec = db.append(conn, kind="pricing.recommended", entity=rfq, payload=payload,
                    sources=[f"ledger:seq{quote[0]['seq']}", f"ledger:seq{assess[0]['seq']}"], **ctx)
    payload["seq"] = rec["seq"]
    return payload


def explain(m: dict) -> list[str]:
    out = [f"{m['version']}: fit on {m['n']} quotes ({m['wins']} won); AUC {m['metrics']['auc']}, "
           f"Brier {m['metrics']['brier']}"]
    for name, b, s in zip(m["features"], m["coef"], m["se"]):
        out.append(f"  {name:22s} {b:+.3f}  (se {s:.3f})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pricing.model", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fit", "recommend"):
        sp = sub.add_parser(name)
        sp.add_argument("--actor", required=True)
        sp.add_argument("--role", default="pricing")
        if name == "recommend":
            sp.add_argument("--rfq", required=True)
            sp.add_argument("--report", action="store_true", help="also render the finance report")
    sub.add_parser("explain")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    conn = db.connect()
    db.init(conn)
    if args.cmd == "fit":
        out = fit_and_record(conn, _ctx(args))
    elif args.cmd == "recommend":
        out = recommend(conn, args.rfq, _ctx(args))
        if args.report:
            from . import report
            out = {"seq": out["seq"], "inputs_sha256": out["inputs_sha256"], "report": report.render(out)}
    else:
        m = json.loads(model_path().read_text(encoding="utf-8"))
        print("\n".join(explain(m)))
        return 0
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
