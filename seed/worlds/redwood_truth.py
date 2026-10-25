"""How Redwood's customers really decide. HIDDEN from the desk: only the simulator and the scorer read this.

A customer who receives a quote accepts it with probability

    P(win) = logistic( intercept + customer effect
                       + price_coef(urgency, wtp) * log(markup)          markup = unit price / Redwood's cost
                       + qty_coef * log10(qty)
                       + urgency, willingness-to-pay, growth and repeat effects )

Price sensitivity is weaker when the customer is in a hurry and stronger when they are price-shopping.
The desk has to learn all of this from the quote history; it never sees these numbers.
"""

from __future__ import annotations

import math

INTERCEPT = 2.3
PRICE_COEF = -7.0            # per unit of log(markup)
PRICE_COEF_URGENT = +2.0     # added when urgency is high: a line-down buyer is less price-sensitive
PRICE_COEF_SHOPPING = -3.5   # added when willingness to pay is low: a price-shopper is more sensitive
QTY_COEF = -0.18             # per decade of quantity: big buys get shopped harder
EFFECTS = {
    "urgency": {"high": 0.9, "normal": 0.0, "low": -0.35},
    "wtp": {"high": 0.7, "normal": 0.0, "low": -0.6},
    "growth": {"growing": 0.25, "flat": 0.0, "shrinking": -0.25},
}
REPEAT = 0.4                 # an existing customer is more likely to accept

# Per customer: a small hidden preference, and the traits that drive the labels in their history.
#   wtp: probabilities of (low, normal, high) on a given quote; urgent: chance a quote is a line-down rush;
#   growth: the customer's trajectory; volume: how often they ask for quotes; qty: typical quantity (median).
CUSTOMERS = {
    "Halcyon Aerospace": {"effect": 0.25, "wtp": (0.05, 0.45, 0.50), "urgent": 0.25, "growth": "growing",
                          "volume": 1.4, "qty": 250},
    "Brightline Medical Devices": {"effect": 0.30, "wtp": (0.05, 0.50, 0.45), "urgent": 0.10, "growth": "flat",
                                   "volume": 1.2, "qty": 500},
    "Tessellate Robotics": {"effect": -0.10, "wtp": (0.55, 0.40, 0.05), "urgent": 0.20, "growth": "growing",
                            "volume": 1.0, "qty": 120},
    "Northwind Defense Systems": {"effect": 0.10, "wtp": (0.15, 0.55, 0.30), "urgent": 0.05, "growth": "flat",
                                  "volume": 0.9, "qty": 2000},
    "Cobalt Instruments": {"effect": 0.0, "wtp": (0.20, 0.65, 0.15), "urgent": 0.10, "growth": "flat",
                           "volume": 1.0, "qty": 300},
    "Bayview University Robotics Lab": {"effect": -0.20, "wtp": (0.60, 0.35, 0.05), "urgent": 0.05,
                                        "growth": "flat", "volume": 0.5, "qty": 25},
    "Gridpoint Energy": {"effect": 0.05, "wtp": (0.25, 0.60, 0.15), "urgent": 0.15, "growth": "growing",
                         "volume": 1.1, "qty": 1500},
    "Kestrel Drones": {"effect": 0.0, "wtp": (0.35, 0.50, 0.15), "urgent": 0.30, "growth": "growing",
                       "volume": 0.8, "qty": 200},
    "Vireo Wearables": {"effect": -0.15, "wtp": (0.40, 0.50, 0.10), "urgent": 0.05, "growth": "shrinking",
                        "volume": 0.8, "qty": 3000},
    "Summit Industrial Controls": {"effect": -0.25, "wtp": (0.65, 0.30, 0.05), "urgent": 0.10, "growth": "flat",
                                   "volume": 1.0, "qty": 400},
}


def logit_win(markup: float, qty: float, urgency: str, wtp: str, growth: str, repeat: bool,
              customer: str | None = None) -> float:
    coef = PRICE_COEF + (PRICE_COEF_URGENT if urgency == "high" else 0.0) + (PRICE_COEF_SHOPPING if wtp == "low" else 0.0)
    z = (INTERCEPT + coef * math.log(markup) + QTY_COEF * math.log10(max(qty, 1.0))
         + EFFECTS["urgency"][urgency] + EFFECTS["wtp"][wtp] + EFFECTS["growth"][growth]
         + (REPEAT if repeat else 0.0) + (CUSTOMERS.get(customer, {}).get("effect", 0.0) if customer else 0.0))
    return z


def p_win(markup: float, qty: float, urgency: str, wtp: str, growth: str, repeat: bool,
          customer: str | None = None) -> float:
    return 1.0 / (1.0 + math.exp(-logit_win(markup, qty, urgency, wtp, growth, repeat, customer)))
