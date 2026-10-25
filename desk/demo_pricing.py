"""The pricing demo for the Redwood desk, segment by segment. Run it from the Redwood checkout.

    python -m desk.demo_pricing            every segment; press Enter between them
    python -m desk.demo_pricing rush       one segment: rush, curve, finance, learn, proof

Everything shown is read from the live ledger and the files the desk wrote (the finance report and its charts).
"""

from __future__ import annotations

import sys

from desk.demo import ROOT, W, children, conn, kv, last, pause, say, scenario_mail, title  # noqa: F401
from ledgerdb import db

SEGMENTS = ("rush", "curve", "finance", "learn", "proof")


def _rfq(c, sid: str) -> str | None:
    mail = scenario_mail(c, sid)
    q = children(c, mail, "from_mail", "quote") if mail else []
    return q[-1] if q else None


def _money(x) -> str:
    return "-" if x is None else (f"${x:,.4f}" if x < 1 else f"${x:,.2f}")


def rush(c) -> None:
    title("THE RFQ  -  a line-down rush from an aerospace customer  (scenario U02)")
    rfq = _rfq(c, "U02")
    if not rfq:
        say("Not reached yet in this run.")
        return
    mail = scenario_mail(c, "U02")
    m = last(c, mail, "mail.received")["payload"]
    kv("From", m.get("from"))
    kv("Subject", m.get("subject"))
    say("")
    say("The email says:")
    say(m.get("body_excerpt", "")[:420], indent=6)
    a = last(c, rfq, "pricing.assessment")
    if a:
        say("")
        say("The pricing agent's three judgement calls, each with its evidence (it never sets the price):")
        for k, label in (("urgency", "Urgency"), ("wtp", "Willingness to pay"), ("growth", "Account growth")):
            call = a["payload"]["calls"][k]
            ev = call["evidence"][0]
            say(f"{label:<20} {call['value']:<9} \"{ev['says'][:58]}\"", indent=4)
    rec = last(c, rfq, "pricing.recommended")
    if rec:
        mg = rec["payload"]["measured_growth"]
        say("")
        say(f"Measured from the books: {mg['orders_12m']} orders in the last 12 months against "
            f"{mg['orders_prior_12m']} the year before, which reads as {mg['label']}.")
        ln = rec["payload"]["lines"][0]
        r = ln.get("requested") or {}
        say(f"Sourcing (code): {r.get('vendor')}, {r.get('lead_days')} days. " + " ".join(r.get("flags", [])))


def curve(c) -> None:
    title("THE CURVE  -  the model's price breaks, maximizing expected profit")
    rfq = _rfq(c, "U02")
    rec = last(c, rfq, "pricing.recommended") if rfq else None
    if not rec:
        say("Not reached yet in this run.")
        return
    p = rec["payload"]
    m = p["model"]
    say(f"Model: logistic win probability fit on {m['n']} past quotes (AUC {m['metrics']['auc']}); for each quantity "
        f"the price with the highest  P(accept) x qty x margin.  Then never dearer at a larger quantity, and at least a "
        f"{p['margin_floor']:.0%} margin.")
    for ln in p["lines"]:
        say("")
        say(f"{ln['mpn']}  ({ln['qty']:,} requested)")
        neutral = {r["qty"]: r for r in ln.get("neutral", [])}
        say(f"{'qty':>8}  {'price':>9}  {'if calls neutral':>16}  {'cost':>9}  {'margin':>6}  {'P(accept)':>9}  source",
            indent=4)
        for r in ln["schedule"]:
            if r.get("unit_price") is None:
                continue
            mark = " <" if r["qty"] == ln["qty"] else ""
            say(f"{r['qty']:>8,}  {_money(r['unit_price']):>9}  {_money(neutral.get(r['qty'], {}).get('unit_price')):>16}"
                f"  {_money(r['unit_cost']):>9}  {r['margin']:>6.0%}  {r['p_win']:>9.0%}  {r.get('vendor')}{mark}", indent=4)
    rep = db.rows(c, "SELECT payload FROM events WHERE kind = 'mail.sent' AND json_extract(payload,'$.rfq') = ? "
                     "AND json_extract(payload,'$.purpose') = 'pricing_report' ORDER BY seq DESC LIMIT 1", [rfq])
    if rep:
        say("")
        say(f"Finance's report (chart and table): {rep[0]['payload'].get('report_dir')}\\report.html")


def finance(c) -> None:
    title("FINANCE DECIDES  -  by replying to the report email; code reads the reply")
    for sid in ("U02", "U06"):
        rfq = _rfq(c, sid)
        if not rfq:
            continue
        d = db.rows(c, "SELECT kind, actor, payload FROM events WHERE entity = ? AND role = 'finance' "
                       "AND kind IN ('approval.granted','approval.rejected') ORDER BY seq DESC LIMIT 1", [rfq])
        if not d:
            continue
        p = d[0]["payload"]
        say(f"{sid} {rfq}: {d[0]['actor']} replied:")
        say("\"" + p.get("reply_text", "").splitlines()[0][:150] + "\"", indent=6)
        say(f"Recorded as {d[0]['kind']} ({p.get('identity')}).", indent=6)
        sent = last(c, rfq, "quote.sent")
        if sent:
            say(f"Quote emailed to {sent['payload']['to']} (the address on file), valid until "
                f"{sent['payload']['valid_until']}, at exactly the approved prices.", indent=6)
        elif last(c, rfq, "quote.rejected"):
            say("No quote sent: the desk declined to quote, as finance said.", indent=6)
        say("")
    say("Code refuses: a report to anyone but finance; a quote before finance approves; a quote to any address but the "
        "one on file; a price in the agent's cover note; an order at any price but the approved one.")


def learn(c) -> None:
    title("SELF-CORRECTION  -  finance teaches the desk a rule; the next quote follows it  (U07 -> U08)")
    r7, r8 = _rfq(c, "U07"), _rfq(c, "U08")
    if not r7:
        say("Not reached yet in this run.")
        return
    rej = db.rows(c, "SELECT payload FROM events WHERE entity = ? AND kind = 'approval.rejected' AND role = 'finance' "
                     "ORDER BY seq LIMIT 1", [r7])
    if rej:
        say(f"1. Finance rejected the FPGA quote for a university lab: \"{rej[0]['payload'].get('note')}\"")
    ad = db.rows(c, "SELECT payload FROM events e WHERE kind = 'workflow.adopted' AND entity = 'workflow:pricing' "
                    "AND EXISTS (SELECT 1 FROM doc_links l WHERE l.seq = e.seq AND l.rel = 'addresses') ORDER BY seq DESC LIMIT 1")
    if ad:
        p = ad[0]["payload"]
        say(f"2. The pricing agent recorded the feedback, proposed a revision, and it was adopted: pricing v{p['previous']} -> "
            f"v{p['version']}, policy keys added: {', '.join(p.get('policy_changed') or []) or 'none'}.")
    if r8:
        rec = db.rows(c, "SELECT payload FROM events WHERE entity = ? AND kind = 'pricing.recommended' ORDER BY seq LIMIT 1", [r8])
        if rec:
            margins = [r["margin"] for ln in rec[0]["payload"]["lines"] for r in ln["schedule"] if r.get("margin") is not None]
            ok = not db.rows(c, "SELECT 1 FROM events WHERE entity = ? AND kind = 'approval.rejected'", [r8])
            say(f"3. U08, another FPGA request from a price-sensitive buyer: first recommendation's lowest margin "
                f"{min(margins):.0%}; finance approved it {'with no correction' if ok else 'only after a correction'}.")
    say("")
    say("Both FPGA buyers then declined at that margin: the rule cost these deals, and the model's refit now knows it.")


def proof(c) -> None:
    title("PROOF  -  scored against a planted answer key and the hidden demand model")
    from eval import score
    out = score.run(c)
    t = out["tally"]
    say(f"Checks: {t.get('pass', 0)} pass, {t.get('fail', 0)} fail, {t.get('pending', 0)} pending.   "
        f"Ledger hash chain verified: {out['ledger_verified']}")
    regrets = [ch["detail"] for r in out["scenarios"] for ch in r["checks"] if ch["check"].startswith("approved price vs")]
    if regrets:
        say(f"Approved prices vs the true optimum: {', '.join(regrets)}  (share of the best expected profit left on the table).")
    say("")
    say("Real: Odoo 18 books, the Increase sandbox bank, the agents' reading of emails, PDFs and CSVs, the model and "
        "its fit. Simulated: the company, customers, suppliers, the quote history, the emails; and in this benchmark, "
        "finance and the controller (stand-in agents answering from an answer key).")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    for stream in (sys.stdout,):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    chosen = [a for a in args if a in SEGMENTS] or list(SEGMENTS)
    interactive = len(chosen) > 1 and sys.stdin.isatty()
    c = conn()
    for i, seg in enumerate(chosen):
        globals()[seg](c)
        if i < len(chosen) - 1:
            pause(interactive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
