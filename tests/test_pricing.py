"""The pricing model: it learns the hidden demand model from simulated history, recommends near-optimal prices,
and its schedule is a real bulk curve. Sourcing picks suppliers by the stated rule."""

import math

import numpy as np
import pytest

from pricing import features as F
from pricing import model as M
from pricing.sourcing import choose, cost_curve
from seed import redwood_history
from seed.worlds import redwood as W
from seed.worlds import redwood_truth as T

CONTEXTS = [dict(urgency="normal", wtp="normal", growth="flat"),
            dict(urgency="high", wtp="normal", growth="growing"),
            dict(urgency="normal", wtp="low", growth="flat"),
            dict(urgency="low", wtp="high", growth="shrinking")]


@pytest.fixture(scope="module")
def fitted():
    rows = redwood_history.generate(n=3000, seed=3)
    return M.fit_rows(rows, "test", bootstrap=10)


def _true_profit(mk, q, c):
    return T.p_win(mk, q, c["urgency"], c["wtp"], c["growth"], True) * (mk - 1)


def test_fit_recovers_the_direction_of_every_effect(fitted):
    coef = dict(zip(fitted["features"], fitted["coef"]))
    assert coef["log_markup"] < -4            # dearer quotes lose
    assert coef["log_markup_x_urgent"] > 0    # a rush buyer minds price less
    assert coef["log_markup_x_shopper"] < 0   # a price-shopper minds it more
    assert coef["wtp_high"] > 0 and coef["growing"] > 0
    assert fitted["metrics"]["auc"] > 0.75


def test_recommended_markup_is_near_the_true_optimum(fitted):
    grid = np.exp(np.linspace(math.log(1.15), math.log(3.0), 1500))
    for c in CONTEXTS:
        for q in (10, 1000):
            mk = M.best_markup(fitted["coef"], q, c, True, 0.15, M.markup_cap(fitted["support"], c))
            best = max(_true_profit(g, q, c) for g in grid)
            regret = 1 - _true_profit(mk, q, c) / best
            assert regret < 0.04, (c, q, mk, regret)


def test_schedule_is_a_bulk_curve_above_the_floor(fitted):
    offers = next(p for p in W.PRODUCTS if p["mpn"] == "TPS62130RGTR")["offers"]
    curve = cost_curve(offers, M.tiers_for(750))
    margin_floor = 0.30  # high enough to bind at some tiers
    sched = M.schedule(fitted["coef"], curve, CONTEXTS[2], True, 1 / (1 - margin_floor) - 1, 15.0,
                       fitted["bootstrap"], fitted["support"])
    prices = [r["unit_price"] for r in sched if r["unit_price"] is not None]
    assert prices == sorted(prices, reverse=True)
    for r in sched:
        assert 1 - r["unit_cost"] / r["unit_price"] >= margin_floor - 1e-9
        assert r["band"][0] <= r["band"][1]


def test_judgement_moves_the_price_the_right_way(fitted):
    offers = next(p for p in W.PRODUCTS if p["mpn"] == "BME280")["offers"]
    curve = cost_curve(offers, (100,))
    price = lambda c: M.schedule(fitted["coef"], curve, c, True, 0.15, 15.0, support=fitted["support"])[0]["unit_price"]
    neutral = price(F.NEUTRAL)
    assert price({**F.NEUTRAL, "urgency": "high"}) > neutral
    assert price({**F.NEUTRAL, "wtp": "low"}) < neutral


def test_sourcing_rules():
    nrf = next(p for p in W.PRODUCTS if p["mpn"] == "NRF52840-QIAA-R")["offers"]
    # Avalon has none, so Keystone supplies 300 (and it takes 10 days).
    c = choose(nrf, 300)
    assert c["chosen"]["vendor"] == "Keystone Components"
    # Needed in 5 days: nobody can make it; the curve quotes the earliest delivery and says so.
    late = cost_curve(nrf, (300,), days_available=5)[0]
    assert late["vendor"] == "Keystone Components" and any("need-by" in f for f in late["flags"])
    # The end-of-life part: only the broker has it, and that is flagged for approval.
    eol = next(p for p in W.PRODUCTS if p["mpn"] == "MC68HC908GP32CFBE")["offers"]
    b = choose(eol, 50)
    assert not b["chosen"]["authorized"] and any("broker" in f for f in b["flags"])


def test_assessment_needs_bounded_calls_with_evidence():
    good = {"calls": {k: {"value": v, "evidence": [{"source": "gmail:x#body", "says": "..."}]}
                      for k, v in F.NEUTRAL.items()}}
    assert F.assessment_problems(good) == []
    bad = {"calls": {**good["calls"], "urgency": {"value": "extreme", "evidence": []}}}
    problems = F.assessment_problems(bad)
    assert any("urgency must be one of" in p for p in problems)
    assert any("urgency: cite" in p for p in problems)
