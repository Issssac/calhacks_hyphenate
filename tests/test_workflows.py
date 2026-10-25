"""The hard-transition checks and the workflow version lifecycle, without WidgeX."""

import json

import pytest

import checks.ledger_checks as lc
from ledgerdb import db
from workflows import build

ORD = {"actor": "orders-agent", "role": "orders", "workflow": "orders"}
INT = {"actor": "intake-agent", "role": "intake", "workflow": "intake"}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("LEDGER_DB", str(path))
    c = db.connect(path)
    db.init(c)
    return c


def staged(conn, entity, model="sale.order", rid=1, **extra):
    db.append(conn, kind="erp.staged", entity=entity, payload={"model": model, "id": rid, **extra}, **ORD)


def quote(conn, total=1000.0):
    db.append(conn, kind="quote.received", entity="quote:Q-1", **INT)
    db.append(conn, kind="quote.extracted", entity="quote:Q-1", payload={
        "customer": "Pier 39 Boatworks", "total": total,
        "lines": [{"item": "Shackle", "qty": 200, "unit_price": 5.0, "source": "gmail:x#att1:p1"}]}, **INT)


def draft(conn, unit_cost):
    db.append(conn, kind="so.drafted", entity="so:SO-1", links=[{"rel": "from_quote", "to": "quote:Q-1"}],
              payload={"customer": "Pier 39 Boatworks", "total": 1000.0,
                       "lines": [{"item": "Shackle", "qty": 200, "unit_price": 5.0}]}, **ORD)
    db.append(conn, kind="po.drafted", entity="po:PO-1", links=[{"rel": "for_so", "to": "so:SO-1"}],
              payload={"vendor": "Harbor Forge", "total": 200 * unit_cost,
                       "lines": [{"item": "Shackle", "qty": 200, "unit_cost": unit_cost}]}, **ORD)
    staged(conn, "so:SO-1")
    staged(conn, "po:PO-1", "purchase.order", 2)


def test_intake_extracted_requires_citations(conn):
    db.append(conn, kind="quote.received", entity="quote:Q-1", **INT)
    db.append(conn, kind="note.focus", entity="focus:intake", payload={"entity": "quote:Q-1"}, **INT)
    db.append(conn, kind="quote.extracted", entity="quote:Q-1", payload={
        "customer": "X", "total": 10, "lines": [{"item": "A", "qty": 1, "unit_price": 10}]}, **INT)
    assert not lc.intake_extracted()
    db.append(conn, kind="quote.extracted", entity="quote:Q-1", payload={
        "customer": "X", "total": 10, "lines": [{"item": "A", "qty": 1, "unit_price": 10, "source": "gmail:m#body"}]},
        **INT)
    assert lc.intake_extracted()
    result = db.rows(conn, "SELECT payload FROM events WHERE kind = 'check.result' ORDER BY seq DESC LIMIT 1")
    assert result[0]["payload"]["ok"] is True


def test_orders_margin_floor(conn):
    quote(conn)
    db.append(conn, kind="note.focus", entity="focus:orders", payload={"entity": "quote:Q-1"}, **ORD)
    draft(conn, unit_cost=4.6)  # 8% margin, floor is 15%
    assert not lc.orders_drafts_valid()
    last = db.rows(conn, "SELECT payload FROM events WHERE kind='check.result' ORDER BY seq DESC LIMIT 1")[0]
    assert any("floor" in p for p in last["payload"]["problems"])
    db.append(conn, kind="po.amended", entity="po:PO-1", links=[{"rel": "for_so", "to": "so:SO-1"}],
              payload={"vendor": "Harbor Forge", "total": 700.0,
                       "lines": [{"item": "Shackle", "qty": 200, "unit_cost": 3.5}]}, **ORD)
    assert not lc.orders_drafts_valid()  # amended PO not re-staged yet
    staged(conn, "po:PO-1", "purchase.order", 2)
    assert lc.orders_drafts_valid()


def test_orders_quote_tie_out(conn):
    quote(conn, total=1100.0)  # quote says 1100, lines say 1000
    db.append(conn, kind="note.focus", entity="focus:orders", payload={"entity": "quote:Q-1"}, **ORD)
    draft(conn, unit_cost=3.0)
    assert not lc.orders_drafts_valid()


def test_queue_and_focus_checks(conn):
    quote(conn)
    assert not lc.orders_queue_empty()
    db.append(conn, kind="note.focus", entity="focus:orders", payload={"entity": "quote:Q-1"}, **ORD)
    assert lc.orders_focus_set() and lc.orders_focus_is_quote() and not lc.orders_focus_is_approved()


def test_feedback_checks(conn):
    assert lc.orders_feedback_closed() and not lc.orders_has_feedback()
    db.append(conn, kind="feedback.recorded", entity="feedback:F-1",
              payload={"driver": "orders", "type": "overrule", "note": "use contract price"}, **ORD)
    assert lc.orders_has_feedback() and not lc.orders_feedback_closed()
    assert lc.intake_feedback_closed()  # other workflows unaffected


def test_version_lifecycle(conn, tmp_path, monkeypatch):
    for name in ("HARNESS_DIR", "PROPOSED", "HISTORY", "PROMPTS"):
        monkeypatch.setattr(build, name, tmp_path / name.lower())
    monkeypatch.setattr(build, "POLICY", tmp_path / "policy.json")
    monkeypatch.setattr(lc, "ROOT", tmp_path)
    monkeypatch.setattr(build, "PROPOSED", tmp_path / "workflows" / "proposed")
    ctx = {"actor": "orders-agent", "role": "orders", "workflow": "orders"}

    from ledgerdb import manifest
    manifest.record(conn, "lead", "architect", "test")
    out = build.compile_(["orders"])
    assert out[0]["from"] == "template"
    assert not lc.orders_proposal_valid()  # not validated yet
    build.record_validation(conn, "orders", True, {"problems": [], "readback": ["Starts in begin."]}, ctx)
    assert lc.orders_proposal_valid()

    # editing after validation invalidates it
    p = build.PROPOSED / "orders.json"
    spec = json.loads(p.read_text())
    spec["states"]["draft"]["transitions"][1]["question"] += " (edited)"
    p.write_text(json.dumps(spec))
    assert not lc.orders_proposal_valid()
    with pytest.raises(SystemExit):
        build.promote(conn, "orders", ctx, [])
    build.record_validation(conn, "orders", True, None, ctx)

    db.append(conn, kind="feedback.recorded", entity="feedback:F-1", payload={"driver": "orders", "note": "x"}, **ctx)
    with pytest.raises(SystemExit, match="no controller approval"):
        build.promote(conn, "orders", ctx, ["feedback:F-1"], "first version")
    # the first version is covered by the controller's approval of the architect's design
    db.append(conn, actor="architect-agent", role="architect", kind="structure.design", entity="structure:design")
    db.append(conn, actor="lead", role="controller", on_behalf_of="human:controller", kind="approval.granted",
              entity="structure:design")
    r = build.promote(conn, "orders", ctx, ["feedback:F-1"], "first version")
    assert r["version"] == 1
    assert lc.orders_feedback_closed()
    assert (build.HARNESS_DIR / "orders.json").exists() and not p.exists()

    # second version archives the first
    build.compile_(["orders"])
    assert build.compile_(["orders"])[0]["from"] == "live"
    (build.PROPOSED / "prompts" / "orders").mkdir(parents=True)
    (build.PROPOSED / "prompts" / "orders" / "draft.md").write_text("new rule: check contract prices")
    build.record_validation(conn, "orders", True, None, ctx)
    assert "contract prices" in build.diff("orders")
    with pytest.raises(SystemExit, match="no controller approval"):
        build.promote(conn, "orders", ctx, [], "contract prices")
    # the proposer can't approve its own revision
    db.append(conn, actor="orders-agent", role="controller", on_behalf_of="human:controller",
              kind="approval.granted", entity="workflow:orders")
    db.append(conn, actor="controller-agent", role="controller", on_behalf_of="model:controller",
              kind="approval.granted", entity="workflow:orders")
    r2 = build.promote(conn, "orders", ctx, [], "contract prices")
    assert r2["version"] == 2
    assert (build.HISTORY / "orders" / "v1" / "orders.json").exists()
    assert (build.PROMPTS / "orders" / "draft.md").read_text() == "new rule: check contract prices"
    assert db.rows(conn, "SELECT version FROM workflow_current")[0]["version"] == 2


def test_ledger_decisions_move_the_driver(conn):
    db.append(conn, kind="so.drafted", entity="so:SO-1", payload={"total": 1}, **ORD)
    assert not lc.orders_awaiting_controller_review()
    db.append(conn, kind="approval.requested", entity="so:SO-1",
              payload={"review_state": "controller_review", "items": ["so:SO-1"]}, **ORD)
    assert lc.orders_awaiting_controller_review()
    assert not lc.orders_decision_approved() and not lc.orders_decision_rejected()
    db.append(conn, actor="controller-agent", role="controller", on_behalf_of="model:controller",
              kind="approval.rejected", entity="so:SO-1", payload={"note": "contract price"})
    assert lc.orders_decision_rejected() and not lc.orders_decision_approved()
    assert not lc.orders_awaiting_controller_review()


def test_reject_closes_feedback(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(build, "PROPOSED", tmp_path / "proposed")
    ctx = {"actor": "orders-agent", "role": "orders", "workflow": "orders"}
    db.append(conn, kind="feedback.recorded", entity="feedback:F-1", payload={"driver": "orders"}, **ctx)
    build.reject(conn, "orders", ctx, ["feedback:F-1"], "one-off: customer typo")
    assert lc.orders_feedback_closed()


@pytest.mark.parametrize("mode", build.APPROVAL_MODES)
def test_every_template_references_real_checks(mode):
    specs = [build.template(d, mode) for d in build.DRIVERS] + [build.controller_template(), build.architect_template()]
    for spec in specs:
        for st in spec["states"].values():
            for t in st.get("transitions", []):
                if t["kind"] == "hard":
                    assert callable(getattr(lc, t["check"]["function"], None)), t["check"]["function"]


def test_promote_refuses_weaker_machines_and_threshold_changes(conn, tmp_path, monkeypatch):
    for name in ("HARNESS_DIR", "PROPOSED", "HISTORY", "PROMPTS"):
        monkeypatch.setattr(build, name, tmp_path / name.lower())
    monkeypatch.setattr(build, "POLICY", tmp_path / "policy.json")
    (tmp_path / "policy.json").write_text(json.dumps({"margin_floor": 0.2}))
    live = build.template("orders", "ledger")
    build.HARNESS_DIR.mkdir(parents=True)
    (build.HARNESS_DIR / "orders.json").write_text(json.dumps(live))
    ctx = {"actor": "orders-agent", "role": "orders", "workflow": "orders"}

    weaker = json.loads(json.dumps(live))
    weaker["states"]["draft"]["transitions"] = [t for t in weaker["states"]["draft"]["transitions"] if t["kind"] != "hard"]
    assert build.weakened(live, weaker) == ["removes check 'orders_drafts_valid'"]
    assert build.weakened(live, build.template("orders", "human")) == []  # a card or the ledger: same review point

    build.PROPOSED.mkdir(parents=True)
    (build.PROPOSED / "orders.json").write_text(json.dumps(weaker))
    build.record_validation(conn, "orders", True, None, ctx)
    with pytest.raises(SystemExit, match="removes check"):
        build.promote(conn, "orders", ctx, [])

    (build.PROPOSED / "orders.json").write_text(json.dumps(live))
    (build.PROPOSED / "policy.orders.json").write_text(json.dumps({"margin_floor": 0.05, "contract_prices": {}}))
    build.record_validation(conn, "orders", True, None, ctx)
    with pytest.raises(SystemExit, match="changes existing policy key 'margin_floor'"):
        build.promote(conn, "orders", ctx, [])
    assert build.policy_patch_problems({"contract_prices": {"Bay Rigging Co": {}}}) == []


def test_structural_check_catches_broken_machines():
    spec = build.template("cash", "ledger")
    assert build.structural_problems(spec) == []
    spec["states"]["apply"]["transitions"][0]["check"]["function"] = "no_such_check"
    spec["states"]["orphan"] = {"prompt": "x", "transitions": [{"to": "nowhere", "precedence": 10, "kind": "soft", "question": "?"}]}
    problems = build.structural_problems(spec)
    assert any("no_such_check" in p for p in problems)
    assert any("unknown state 'nowhere'" in p for p in problems)
    assert any("orphan: unreachable" in p for p in problems)


def test_revision_cannot_add_a_way_around_review():
    live = build.template("orders", "ledger")
    sneaky = json.loads(json.dumps(live))
    # keep every control in the file, but let draft go straight to post on the agent's word
    sneaky["states"]["draft"]["transitions"].append(
        {"to": "post", "precedence": 30, "kind": "soft", "question": "Skip review?"})
    problems = build.weakened(live, sneaky, "orders")
    assert any("adds a new way into 'post'" in p for p in problems)
    # and a chain of agent-asserted edges from begin into a write state is refused even in a new machine
    chain = json.loads(json.dumps(live))
    chain["states"]["begin"]["transitions"].append({"to": "post", "precedence": 99, "kind": "soft", "question": "?"})
    assert any("post is reachable from begin without any code check" in p
               for p in build.bypasses(chain, build.WRITE_STATES["orders"]))
    # a hard edge with an unrelated check is still a new way in
    hard = json.loads(json.dumps(live))
    hard["states"]["draft"]["transitions"].append(
        {"to": "post", "precedence": 30, "kind": "hard", "question": "?",
         "check": {"file": build.CHECKS, "function": "orders_queue_empty"}})
    assert any("adds a new way into 'post'" in p for p in build.weakened(live, hard, "orders"))
    for d in build.DRIVERS:  # the templates themselves are clean
        assert build.bypasses(build.template(d, "ledger"), build.WRITE_STATES[d]) == []


def test_manifest_catches_edited_control_code(conn, tmp_path, monkeypatch):
    from ledgerdb import manifest
    f = tmp_path / "ledger_checks.py"
    f.write_text("def check(): return False")
    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    monkeypatch.setattr(manifest, "CONTROL_FILES", ["ledger_checks.py"])
    assert manifest.changes(conn)[0]["problem"] == "no manifest recorded"
    manifest.record(conn, "lead", "architect")
    assert manifest.changes(conn) == []
    f.write_text("def check(): return True  # quietly loosened")
    assert manifest.changes(conn) == [{"file": "ledger_checks.py", "problem": "changed since the manifest was recorded"}]
    assert any("control file ledger_checks.py" in p for p in build.manifest_problems(conn))


def test_hooks_mode_has_no_idle_loop_and_weakens_nothing():
    for d in build.DRIVERS:
        poll, hooks = build.template(d, "ledger", "poll"), build.template(d, "ledger", "hooks")
        assert "wait" not in hooks["states"]
        assert hooks["harness"]["triggers"][0]["id"] == build.HOOK_IDS[d]
        assert build.structural_problems(hooks) == [], d
        assert build.weakened(poll, hooks, d) == [], d  # switching to hooks removes no control
        # every review point can end the run instead of polling
        for name, st in hooks["states"].items():
            if any("_decision_approved" in (t.get("check") or {}).get("function", "") for t in st["transitions"]):
                assert any(t["to"] == "done" and t["kind"] == "soft" for t in st["transitions"])


def test_woken_driver_goes_to_the_decision(conn):
    db.append(conn, kind="so.drafted", entity="so:SO-1", payload={"total": 1}, **ORD)
    db.append(conn, kind="approval.requested", entity="so:SO-1", payload={"review_state": "controller_review"}, **ORD)
    assert not lc.orders_decided_controller_review_approved()
    db.append(conn, actor="controller-agent", role="controller", on_behalf_of="model:controller",
              kind="approval.granted", entity="so:SO-1", payload={"note": "ok"})
    assert lc.orders_decided_controller_review_approved()
    assert not lc.orders_decided_controller_review_rejected()
    # once orders acts on it (posts), it no longer counts as new
    db.append(conn, kind="erp.posted", entity="so:SO-1", payload={"model": "sale.order", "id": 1}, **ORD)
    assert not lc.orders_decided_controller_review_approved()
    assert lc.controller_nothing_open()
