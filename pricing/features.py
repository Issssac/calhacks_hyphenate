"""The model's inputs: the agent's judgement calls, checked, and the numbers the ledger can compute itself.

The pricing agent makes three calls about the customer for each RFQ, and must cite evidence for each:

    growth    growing | flat | shrinking    is the account buying more than a year ago?
    wtp       low | normal | high           willingness to pay: price-shopping, or paying for service?
    urgency   low | normal | high           is this a line-down rush, a routine buy, or a budgetary ask?

The calls are bounded categories, never numbers. How much each call moves the price is learned from the quote
history by the model; the agent cannot dial a price directly. Growth is also measured from the books
(`measured_growth`), and when the agent's call disagrees with the measurement, the report says so.
"""

from __future__ import annotations

import csv
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

CALLS = {
    "growth": ("growing", "flat", "shrinking"),
    "wtp": ("low", "normal", "high"),
    "urgency": ("low", "normal", "high"),
}
NEUTRAL = {"growth": "flat", "wtp": "normal", "urgency": "normal"}

# The model's design. Fit and prediction both build rows with `row()`, so they cannot drift apart.
FEATURES = ("const", "log_markup", "log10_qty", "urgency_high", "urgency_low", "wtp_high", "wtp_low",
            "growing", "shrinking", "repeat", "log_markup_x_urgent", "log_markup_x_shopper")

GROWTH_BAND = 0.15  # measured change beyond +/-15% year on year counts as growing / shrinking


def row(markup: float, qty: float, urgency: str, wtp: str, growth: str, repeat: bool) -> list[float]:
    lm = math.log(markup)
    return [1.0, lm, math.log10(max(qty, 1.0)),
            float(urgency == "high"), float(urgency == "low"), float(wtp == "high"), float(wtp == "low"),
            float(growth == "growing"), float(growth == "shrinking"), float(bool(repeat)),
            lm * (urgency == "high"), lm * (wtp == "low")]


def assessment_problems(a: dict) -> list[str]:
    """What is wrong with an assessment payload, or [] if it can be used."""
    problems = []
    calls = a.get("calls") if isinstance(a, dict) else None
    if not isinstance(calls, dict):
        return ["assessment has no `calls` object"]
    for name, allowed in CALLS.items():
        c = calls.get(name)
        if not isinstance(c, dict):
            problems.append(f"no call for {name}")
            continue
        if c.get("value") not in allowed:
            problems.append(f"{name} must be one of {', '.join(allowed)} (got {c.get('value')!r})")
        ev = c.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(e, dict) and e.get("source") and e.get("says")
                                                         for e in ev):
            problems.append(f"{name}: cite at least one piece of evidence as {{\"source\": ..., \"says\": ...}}")
    return problems


def values(a: dict) -> dict[str, str]:
    return {k: a["calls"][k]["value"] for k in CALLS}


# ---------------------------------------------------------------- what the books say

def _history_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def measured_growth(customer: str, history: Path, conn=None, as_of: date | None = None) -> dict[str, Any]:
    """Ordered value in the last 12 months against the 12 before, from the quote history and the ledger's SOs."""
    as_of = as_of or date.today()
    y1, y2 = as_of - timedelta(days=365), as_of - timedelta(days=730)
    recent = prior = 0.0          # ordered value
    n_recent = n_prior = 0        # orders won
    for r in _history_rows(history):
        if r["customer"] != customer or not int(r["won"]):
            continue
        d = date.fromisoformat(r["date"])
        v = float(r["unit_price"]) * float(r["ordered_qty"])
        if y1 < d <= as_of:
            recent += v
            n_recent += 1
        elif y2 < d <= y1:
            prior += v
            n_prior += 1
    if conn is not None:
        import json as _json
        for e in conn.execute("SELECT ts, payload FROM events WHERE kind = 'so.drafted'"):
            p = _json.loads(e[1])
            if p.get("customer") != customer:
                continue
            d = date.fromisoformat(e[0][:10])
            if y1 < d <= as_of:
                recent += float(p.get("total") or 0)
                n_recent += 1
    # The label follows the number of orders, which a few very large orders can't swing; value is reported too.
    change = (n_recent - n_prior) / n_prior if n_prior else (1.0 if n_recent else 0.0)
    value_change = (recent - prior) / prior if prior else (1.0 if recent else 0.0)
    label = "growing" if change > GROWTH_BAND else ("shrinking" if change < -GROWTH_BAND else "flat")
    return {"trailing_12m": round(recent, 2), "prior_12m": round(prior, 2), "value_change": round(value_change, 3),
            "orders_12m": n_recent, "orders_prior_12m": n_prior, "change": round(change, 3), "label": label}


def is_repeat(customer: str, history: Path) -> bool:
    return any(r["customer"] == customer and int(r["won"]) for r in _history_rows(history))
