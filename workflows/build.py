"""Build, check-in and promote the driver workflows (WidgeX harness machines).

Lifecycle of a workflow version:

    compile   templates (or the live version, if one exists) -> workflows/proposed/<driver>.json
    validate  the agent runs api.harness_check on the proposed file, then records the result here,
              keyed by the file's sha256, so an edit after checking invalidates it
    diff      what would change, for the review card
    promote   after a person approves: archive the live version under workflows/history/<driver>/v<N>/,
              make the proposal live, record workflow.adopted (linked to the feedback it answers)
    reject    record workflow.rejected and discard the proposal

    python -m workflows.build compile [driver ...]
    python -m workflows.build record-validation <driver> --ok true --actor architect-1 --role architect < check.json
    python -m workflows.build diff <driver>
    python -m workflows.build promote <driver> --actor orders-agent --role orders --addresses feedback:F-1
    python -m workflows.build reject <driver> --reason "..." --addresses feedback:F-1 --actor ... --role ...
    python -m workflows.build current

Every check path and prompt path is written absolute, so the machine works
from whichever directory WidgeX runs it.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledgerdb import db  # noqa: E402

HARNESS_DIR = ROOT / ".widgex" / "harnesses"
PROPOSED = ROOT / "workflows" / "proposed"
HISTORY = ROOT / "workflows" / "history"
PROMPTS = ROOT / "workflows" / "prompts"
POLICY = ROOT / "structure" / "policy.json"
# WidgeX resolves check.file relative to the harness file's folder and refuses anything outside
# it, so the workflows point at a shim in .widgex/harnesses/checks/ that loads checks/ledger_checks.py.
CHECKS = "checks/ledger_checks.py"
DRIVERS = ("intake", "orders", "billing", "cash", "reconcile")
ROLE = {"intake": "intake", "orders": "orders", "billing": "billing", "cash": "cash", "reconcile": "reconcile",
        "architect": "architect", "controller": "controller"}
TAIL = ("overruled", "revise", "adopt", "promote")


# ---------------------------------------------------------------- template pieces

def _pointer(driver: str, state: str) -> str:
    own = (PROMPTS / driver / f"{state}.md").as_posix()
    shared = (PROMPTS / "_shared.md").as_posix()
    tail = (PROMPTS / "_tail" / f"{state}.md").as_posix()
    where = f"Read {own} and follow it exactly."
    if state in TAIL or state in ("done", "wait"):
        where = f"Read {own} if it exists, otherwise {tail}, and follow it exactly."
    return (f"Revenue desk / workflow `{driver}` / state `{state}`.\n{where}\n"
            f"The rules in {shared} apply throughout. You are actor `{driver}-agent` with role `{ROLE[driver]}`; "
            f"pass --workflow {driver} on every ledger write.")


def hard(to: str, p: int, q: str, fn: str) -> dict:
    return {"to": to, "precedence": p, "kind": "hard", "question": q, "check": {"file": CHECKS, "function": fn}}


def soft(to: str, p: int, q: str) -> dict:
    return {"to": to, "precedence": p, "kind": "soft", "question": q}


def human(to: str, p: int, q: str, decision: str) -> dict:
    return {"to": to, "precedence": p, "kind": "human", "decision": decision, "question": q}


def review(approve_to: str, approve_q: str, reject_to: str, reject_q: str) -> dict:
    """A point where the controller decides. Rendered per approval mode in _finish()."""
    return {"_review": (approve_to, approve_q, reject_to, reject_q)}


# How the controller decides:
#   human   a WidgeX review card (a person clicks Approve / Send back on the Harnesses page)
#   ledger  the driver records approval.requested and waits; the controller (a person through
#           `python -m bench.controller decide`, or the benchmark controller agent) records
#           approval.granted / approval.rejected, and a code check moves the driver on.
APPROVAL_MODES = ("human", "ledger")

LEDGER_RULES = (
    "\nApprovals in this desk run through the ledger: there is no review card. Never record approval.granted "
    "or approval.rejected yourself. The controller does, as itself. Skip any step that tells you to record "
    "them on the controller's behalf, and read the controller's note from its approval event "
    "(`python -m ledgerdb state <entity>`).")


def _review_pointer(driver: str, state: str) -> str:
    return (
        "\nThis is a controller decision point. If you came here from `begin`, a request is already open: "
        "find it with `python -m ledgerdb query \"SELECT entity, payload FROM events WHERE kind='approval.requested' "
        f"AND workflow='{driver}' ORDER BY seq DESC LIMIT 1\"`, focus on its item, and skip to waiting. Otherwise, do what "
        "the instructions file says to prepare the request, and record it once on the item being decided: "
        f"`--kind approval.requested` with the payload `{{\"review_state\": \"{state}\", \"summary\": \"<what you'd tell the controller>\", "
        "\"items\": [<every entity the decision covers, e.g. the SO and its POs>], \"recommendation\": \"...\"}`. "
        "Then wait 60 seconds (`await asyncio.sleep(60)`), check the item for an approval.granted or approval.rejected "
        "newer than your request, and answer the questions. Until the controller decides, answer both false. "
        "You'll stay here and check again.")


def _finish(driver: str, states: dict[str, dict], mode: str) -> dict[str, dict]:
    """Render review states for the approval mode, and add prompts."""
    reviews = [n for n, st in states.items() if "_review" in st]
    for name in reviews:
        a_to, a_q, r_to, r_q = states[name].pop("_review")
        if mode == "human":
            states[name].update({"review": {}, "transitions": [human(a_to, 10, a_q, "approve"),
                                                                human(r_to, 20, r_q, "reject")]})
        else:
            states[name]["transitions"] = [
                hard(a_to, 10, f"Has the controller approved it (approval.granted after your request)? ({a_q})",
                     f"{driver}_decision_approved"),
                hard(r_to, 20, f"Has the controller rejected it (approval.rejected after your request)? ({r_q})",
                     f"{driver}_decision_rejected")]
    if mode == "ledger" and "begin" in states and driver in DRIVERS:
        # A restarted driver goes straight back to any decision it is still waiting on.
        for i, name in enumerate(reviews):
            states["begin"]["transitions"].insert(
                1 + i, hard(name, 6 + i, f"Is one of your requests still waiting on the controller at `{name}`?",
                            f"{driver}_awaiting_{name}"))
    for name, st in states.items():
        st["prompt"] = _pointer(driver, name) + (LEDGER_RULES if mode == "ledger" else "") + \
            (_review_pointer(driver, name) if mode == "ledger" and name in reviews else "")
    return states


def _feedback_edge(driver: str) -> dict:
    return hard("revise", 5, "Is there open feedback for this workflow (`python -m ledgerdb feedback`)?",
                f"{driver}_has_feedback")


def _tail(driver: str) -> dict[str, dict]:
    return {
        "overruled": {"transitions": [
            hard("revise", 10, "Did you apply the controller's correction to the item and record feedback.recorded?",
                 f"{driver}_has_feedback")]},
        "revise": {"transitions": [
            hard("adopt", 10, "Did you write a revision, check it with api.harness_check and record "
                              "workflow.proposed and workflow.validated for that exact file?",
                 f"{driver}_proposal_valid"),
            hard("begin", 20, "Did you decide no workflow change is needed and record workflow.rejected "
                              "answering the feedback?", f"{driver}_feedback_closed")]},
        "adopt": review("promote", "Adopt this revision of the workflow?",
                        "revise", "Send it back for another revision (say what is wrong in the note)?"),
        "promote": {"transitions": [
            hard("begin", 10, "Did you run `python -m workflows.build promote` and does it report the new version?",
                 f"{driver}_feedback_closed")]},
        "wait": {"transitions": [
            hard("done", 10, "Has the whole desk been idle long enough to stop (see policy idle_minutes)?",
                 "desk_idle"),
            soft("begin", 20, "Have you waited, and are you ready to look for work again?")]},
        "done": {"terminal": True, "transitions": []},
    }


def template(driver: str, mode: str = "human") -> dict:
    d = driver
    if d == "intake":
        states = {
            "begin": {"transitions": [
                _feedback_edge(d),
                hard("classify", 10, "Did you pick an item from the intake queue and record it as your focus?",
                     "intake_focus_set"),
                hard("wait", 20, "Is the intake queue empty after pulling new mail?", "intake_queue_empty")]},
            "classify": {"transitions": [
                soft("extract", 10, "Is the focus a customer quote or amendment that now needs extracting?"),
                hard("begin", 20, "Was it something else, now classified and handed off?", "intake_handled")]},
            "extract": {"transitions": [
                hard("begin", 10, "Did you record a clean quote.extracted with every line cited?", "intake_extracted"),
                soft("clarify", 20, "Did you record a quote.conflict that needs the controller to decide?")]},
            "clarify": review("resolve", "Accept the agent's recommended resolution of this conflict?",
                              "overruled", "Reject the recommendation (write the correct values in the note)?"),
            "resolve": {"transitions": [
                hard("begin", 10, "Did you record quote.clarified with the approved resolution?", "intake_extracted")]},
        }
    elif d == "orders":
        states = {
            "begin": {"transitions": [
                _feedback_edge(d),
                hard("post", 10, "Is your focus an order that is already approved and only needs confirming in Odoo?",
                     "orders_focus_is_approved"),
                hard("draft", 20, "Is your focus a quote that needs a sales order (new or amended), or an SO still waiting to be checked and sent for approval?",
                     "orders_focus_is_quote"),
                hard("wait", 30, "Is the orders queue empty?", "orders_queue_empty")]},
            "draft": {"transitions": [
                hard("controller_review", 10, "Are the SO and its POs drafted and staged, passing every check?",
                     "orders_drafts_valid"),
                soft("controller_review", 20, "Are they drafted and staged but breaking a rule you have flagged "
                                              "for the controller?")]},
            "controller_review": review("post", "Approve this sales order and its purchase orders?",
                                        "overruled", "Reject them (say what is wrong in the note)?"),
            "post": {"transitions": [
                hard("begin", 10, "Are the SO and all its POs confirmed in Odoo?", "orders_posted")]},
        }
    elif d == "billing":
        states = {
            "begin": {"transitions": [
                _feedback_edge(d),
                hard("invoice", 10, "Is your focus a confirmed sales order that needs invoicing?", "billing_focus_is_so"),
                hard("bill", 20, "Is your focus a vendor bill to record or match?", "billing_focus_is_bill"),
                hard("wait", 30, "Is there nothing actionable in the billing queue?", "billing_queue_empty")]},
            "invoice": {"transitions": [
                hard("begin", 10, "Is the invoice posted and equal to the sales order?", "billing_invoice_posted"),
                soft("invoice_review", 20, "Does the invoice disagree with the SO or tax policy in a way you "
                                           "cannot resolve yourself?")]},
            "invoice_review": review("invoice_fix", "Approve the agent's proposed fix to this invoice?",
                                     "overruled", "Reject it (say what is right in the note)?"),
            "invoice_fix": {"transitions": [
                hard("begin", 10, "Is the corrected invoice posted?", "billing_invoice_posted")]},
            "bill": {"transitions": [
                hard("bill_post", 10, "Did the code 3-way match pass (bill.matched)?", "billing_bill_matched"),
                soft("bill_review", 20, "Did the 3-way match record a mismatch for the controller?")]},
            "bill_review": review("bill_post", "Accept the variance and post the bill as billed?",
                                  "overruled", "Reject it (dispute or correct; say how in the note)?"),
            "bill_post": {"transitions": [
                hard("begin", 10, "Is the bill posted in Odoo?", "billing_bill_posted")]},
        }
    elif d == "cash":
        states = {
            "begin": {"transitions": [
                _feedback_edge(d),
                hard("apply", 10, "Is your focus money received that needs matching to invoices?",
                     "cash_focus_is_receipt"),
                hard("send_payment", 20, "Is your focus an approved vendor payment that has not been sent?",
                     "cash_focus_is_approved_payment"),
                hard("propose_payment", 30, "Is your focus an open vendor bill with no payment proposed?",
                     "cash_focus_is_payable"),
                hard("wait", 40, "Is there nothing actionable in the cash queue?", "cash_queue_empty")]},
            "apply": {"transitions": [
                hard("begin", 10, "Is the payment registered in Odoo with every dollar applied or explained?",
                     "cash_payment_applied"),
                soft("cash_review", 20, "Is it ambiguous, disputed or unidentifiable, needing the controller?")]},
            "cash_review": review("apply_approved", "Approve the agent's recommended application of this money?",
                                  "overruled", "Reject it (say how to apply it in the note)?"),
            "apply_approved": {"transitions": [
                hard("begin", 10, "Is the payment registered as approved?", "cash_payment_applied")]},
            "propose_payment": {"transitions": [
                hard("payment_review", 10, "Did you draft the bill payment and request approval?",
                     "cash_billpayment_proposed")]},
            "payment_review": review("send_payment", "Approve sending this vendor payment?",
                                     "overruled", "Reject it (say why in the note)?"),
            "send_payment": {"transitions": [
                hard("begin", 10, "Was the payment sent from the bank and registered in Odoo?",
                     "cash_billpayment_done")]},
        }
    elif d == "reconcile":
        states = {
            "begin": {"transitions": [
                _feedback_edge(d),
                hard("wait", 10, "Did reconciliation tie with nothing outstanding?", "recon_ok"),
                soft("investigate", 20, "Did reconciliation report differences?")]},
            "investigate": {"transitions": [
                hard("report", 10, "Did you record a recon.finding for every difference?", "recon_findings_recorded")]},
            "report": review("wait", "Accept these reconciliation findings?",
                             "overruled", "Reject them (say what is wrong or what to look at in the note)?"),
        }
    else:
        raise ValueError(f"no template for {driver!r}")
    states.update(_tail(d))
    states = _finish(d, states, mode)
    ordered = {"begin": states.pop("begin"), **states}
    for name, st in ordered.items():
        ordered[name] = {"prompt": st["prompt"], **{k: v for k, v in st.items() if k != "prompt"}}
    return {"version": 2, "name": d, "description": f"Revenue desk {d} driver", "states": ordered}


def controller_template() -> dict:
    """Benchmark stand-in for the human controller: answers ledger approval requests from the answer key."""
    states = {
        "begin": {"transitions": [
            hard("done", 10, "Is the whole desk idle, with no approval request left open?", "controller_idle")]},
        "done": {"terminal": True, "transitions": []},
    }
    states = _finish("controller", states, "human")
    return {"version": 2, "name": "controller",
            "description": "Benchmark controller: decides approval requests from the answer key",
            "states": {n: {"prompt": s["prompt"], **{k: v for k, v in s.items() if k != "prompt"}}
                       for n, s in states.items()}}


def architect_template() -> dict:
    """The first workflow: investigate the books, design the pipeline, propose it, start it."""
    states = {
        "begin": {"transitions": [
            soft("design", 10, "Have you surveyed Odoo, the bank and the inbox, and recorded what you found "
                               "as structure.* events?")]},
        "design": {"transitions": [
            hard("compile", 10, "Have you written company_profile.md, policy.json and pipeline.json?",
                 "architect_profile_written")]},
        "compile": {"transitions": [
            hard("propose", 10, "Is every enabled driver compiled, checked with api.harness_check, and recorded?",
                 "architect_all_validated")]},
        "propose": review("spawn", "Adopt this pipeline and start its agents?",
                          "design", "Send the design back (say what to change in the note)?"),
        "spawn": {"transitions": [
            soft("done", 10, "Did you promote every driver and start its agent, or rest because starting was refused?")]},
        "done": {"terminal": True, "transitions": []},
    }
    states = _finish("architect", states, "human")
    out = {}
    for name, st in states.items():
        out[name] = {"prompt": st["prompt"], **{k: v for k, v in st.items() if k != "prompt"}}
    return {"version": 2, "name": "architect",
            "description": "Investigates the books and compiles the revenue desk's driver workflows", "states": out}

# ---------------------------------------------------------------- helpers

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_path(driver: str) -> Path:
    return HARNESS_DIR / f"{driver}.json"


def current_version(conn, driver: str) -> int:
    row = conn.execute("SELECT version FROM workflow_current WHERE entity = ?", (f"workflow:{driver}",)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def approval_mode() -> str:
    pipeline = ROOT / "structure" / "pipeline.json"
    mode = json.loads(pipeline.read_text(encoding="utf-8")).get("approvals", "human") if pipeline.exists() else "human"
    return mode if mode in APPROVAL_MODES else "human"


def compile_(drivers: list[str], from_template: bool = False, mode: str | None = None) -> list[dict]:
    PROPOSED.mkdir(parents=True, exist_ok=True)
    mode = mode or approval_mode()
    out = []
    for d in drivers:
        src = live_path(d)
        spec = (json.loads(src.read_text(encoding="utf-8")) if src.exists() and not from_template
                else template(d, mode))
        dest = PROPOSED / f"{d}.json"
        dest.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        out.append({"driver": d, "from": "live" if src.exists() else "template", "path": str(dest),
                    "sha256": sha(dest), "states": list(spec["states"])})
    return out


def record_validation(conn, driver: str, ok: bool, check: dict | None, ctx: dict) -> dict:
    path = PROPOSED / f"{driver}.json"
    return db.append(conn, kind="workflow.validated", entity=f"workflow:{driver}", payload={
        "file": str(path), "sha256": sha(path), "ok": ok,
        "problems": (check or {}).get("problems", []), "readback": (check or {}).get("readback", [])}, **ctx)


def _prompt_files(base: Path, driver: str) -> dict[str, str]:
    d = base / driver
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))} if d.exists() else {}


def diff(driver: str) -> str:
    live = live_path(driver)
    prop = PROPOSED / f"{driver}.json"
    a = live.read_text(encoding="utf-8").splitlines() if live.exists() else []
    b = prop.read_text(encoding="utf-8").splitlines() if prop.exists() else []
    parts = ["\n".join(difflib.unified_diff(a, b, f"live/{driver}.json", f"proposed/{driver}.json", lineterm=""))]
    old, new = _prompt_files(PROMPTS, driver), _prompt_files(PROPOSED / "prompts", driver)
    for name, text in new.items():
        parts.append("\n".join(difflib.unified_diff(
            old.get(name, "").splitlines(), text.splitlines(),
            f"live/prompts/{driver}/{name}", f"proposed/prompts/{driver}/{name}", lineterm="")))
    patch = PROPOSED / f"policy.{driver}.json"
    if patch.exists():
        cur = POLICY.read_text(encoding="utf-8") if POLICY.exists() else "{}"
        merged = json.dumps({**json.loads(cur), **json.loads(patch.read_text(encoding="utf-8"))}, indent=1)
        parts.append("\n".join(difflib.unified_diff(cur.splitlines(), merged.splitlines(),
                                                     "live/structure/policy.json", "proposed/structure/policy.json",
                                                     lineterm="")))
    return "\n\n".join(p for p in parts if p.strip()) or "(no differences)"


def promote(conn, driver: str, ctx: dict, addresses: list[str], summary: str = "") -> dict:
    prop = PROPOSED / f"{driver}.json"
    if not prop.exists():
        raise SystemExit(f"nothing proposed for {driver}")
    s = sha(prop)
    ok = conn.execute("SELECT 1 FROM events WHERE kind = 'workflow.validated' AND entity = ?"
                      " AND json_extract(payload, '$.sha256') = ? AND json_extract(payload, '$.ok') = 1",
                      (f"workflow:{driver}", s)).fetchone()
    if not ok:
        raise SystemExit(f"{prop} was not validated in this exact form; run harness_check and record it")
    cur = current_version(conn, driver)
    diff_text = diff(driver)
    live = live_path(driver)
    if live.exists():
        arch = HISTORY / driver / f"v{cur}"
        arch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, arch / f"{driver}.json")
        if (PROMPTS / driver).exists():
            shutil.copytree(PROMPTS / driver, arch / "prompts", dirs_exist_ok=True)
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    spec = json.loads(prop.read_text(encoding="utf-8"))
    spec["description"] = f"Revenue desk {driver} driver (v{cur + 1})"
    live.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    new_prompts = PROPOSED / "prompts" / driver
    changed_prompts = []
    if new_prompts.exists():
        (PROMPTS / driver).mkdir(parents=True, exist_ok=True)
        for p in new_prompts.glob("*.md"):
            shutil.copy2(p, PROMPTS / driver / p.name)
            changed_prompts.append(p.name)
        shutil.rmtree(new_prompts)
    prop.unlink()
    patch = PROPOSED / f"policy.{driver}.json"
    policy_changed = []
    if patch.exists():
        changes = json.loads(patch.read_text(encoding="utf-8"))
        current = json.loads(POLICY.read_text(encoding="utf-8")) if POLICY.exists() else {}
        if POLICY.exists():
            (HISTORY / driver / f"v{cur}").mkdir(parents=True, exist_ok=True)
            shutil.copy2(POLICY, HISTORY / driver / f"v{cur}" / "policy.json")
        POLICY.write_text(json.dumps({**current, **changes}, indent=1), encoding="utf-8")
        policy_changed = sorted(changes)
        patch.unlink()
    ev = db.append(conn, kind="workflow.adopted", entity=f"workflow:{driver}", payload={
        "version": cur + 1, "previous": cur, "file": str(live), "sha256": sha(live), "proposal_sha256": s,
        "prompts_changed": changed_prompts, "policy_changed": policy_changed, "summary": summary, "diff": diff_text[:20000],
        "archived": str(HISTORY / driver / f"v{cur}") if cur else None},
        links=[{"rel": "addresses", "to": a} for a in addresses], **ctx)
    return {"driver": driver, "version": cur + 1, "live": str(live), "event": ev}


def reject(conn, driver: str, ctx: dict, addresses: list[str], reason: str) -> dict:
    prop = PROPOSED / f"{driver}.json"
    ev = db.append(conn, kind="workflow.rejected", entity=f"workflow:{driver}",
                   payload={"reason": reason, "proposal_sha256": sha(prop) if prop.exists() else None},
                   links=[{"rel": "addresses", "to": a} for a in addresses], **ctx)
    if prop.exists():
        prop.unlink()
    shutil.rmtree(PROPOSED / "prompts" / driver, ignore_errors=True)
    return {"driver": driver, "rejected": ev}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="workflows.build", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile")
    c.add_argument("drivers", nargs="*")
    c.add_argument("--from-template", action="store_true",
                   help="rebuild from the template (e.g. after changing the approval mode), not the live version")
    c.add_argument("--approvals", choices=APPROVAL_MODES, help="default: structure/pipeline.json 'approvals'")

    def ctx_args(sp):
        sp.add_argument("--actor", required=True)
        sp.add_argument("--role", required=True)
        return sp

    rv = ctx_args(sub.add_parser("record-validation"))
    rv.add_argument("driver")
    rv.add_argument("--ok", choices=["true", "false"], required=True)
    rv.add_argument("--check", default="-", help="harness_check result JSON: @file, - for stdin, or '' for none")
    sub.add_parser("diff").add_argument("driver")
    pr = ctx_args(sub.add_parser("promote"))
    pr.add_argument("driver")
    pr.add_argument("--addresses", action="append", default=[])
    pr.add_argument("--summary", default="")
    rj = ctx_args(sub.add_parser("reject"))
    rj.add_argument("driver")
    rj.add_argument("--addresses", action="append", default=[])
    rj.add_argument("--reason", required=True)
    sub.add_parser("current")
    sub.add_parser("architect", help="(re)write .widgex/harnesses/architect.json")
    sub.add_parser("controller", help="(re)write .widgex/harnesses/controller.json (benchmark controller)")
    args = p.parse_args(argv)
    out = lambda o: sys.stdout.write((o if isinstance(o, str) else json.dumps(o, indent=2, default=str)) + "\n")

    if args.cmd == "compile":
        pipeline = ROOT / "structure" / "pipeline.json"
        drivers = args.drivers or ([d["name"] for d in json.loads(pipeline.read_text(encoding="utf-8"))["drivers"]
                                    if d.get("enabled", True)] if pipeline.exists() else list(DRIVERS))
        out(compile_(drivers, args.from_template, args.approvals))
        return 0
    if args.cmd == "diff":
        out(diff(args.driver))
        return 0
    if args.cmd in ("architect", "controller"):
        HARNESS_DIR.mkdir(parents=True, exist_ok=True)
        path = HARNESS_DIR / f"{args.cmd}.json"
        spec = architect_template() if args.cmd == "architect" else controller_template()
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        out({"path": str(path), "sha256": sha(path)})
        return 0
    conn = db.connect()
    if args.cmd == "current":
        out(db.rows(conn, "SELECT entity, version, sha256, ts FROM workflow_current ORDER BY entity"))
        return 0
    ctx = {"actor": args.actor, "role": args.role, "workflow": getattr(args, "driver", None)}
    try:
        if args.cmd == "record-validation":
            check = None
            if args.check == "-":
                text = sys.stdin.read().strip()
                check = json.loads(text) if text else None
            elif args.check.startswith("@"):
                check = json.loads(Path(args.check[1:]).read_text(encoding="utf-8"))
            out(record_validation(conn, args.driver, args.ok == "true", check, ctx))
        elif args.cmd == "promote":
            out(promote(conn, args.driver, ctx, args.addresses, args.summary))
        elif args.cmd == "reject":
            out(reject(conn, args.driver, ctx, args.addresses, args.reason))
    except db.LedgerError as e:
        out({"error": str(e)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
