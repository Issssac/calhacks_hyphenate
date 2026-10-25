"""The 2-minute demo, segment by segment, for whoever screen-records it.

    python -m desk.demo              every segment in order; press Enter between them
    python -m desk.demo moment       one segment: moment, what, gate, learn, proof, phish, close
    python -m desk.demo --check      say which segments the current run can show yet

Everything shown is read from the live ledger, Odoo and the bank. The gate demo uses a
throwaway ledger so nothing real is changed. Spoken lines are in docs/demo-narration.md.
Make the terminal font large (24pt+) and the window about 110 columns wide.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledgerdb import db  # noqa: E402
from seed.sim import load as load_sim  # noqa: E402

W = 100
SEGMENTS = ("moment", "what", "gate", "learn", "proof", "phish", "close")


# ---------------------------------------------------------------- printing

def title(t: str) -> None:
    print("\n" + "=" * W)
    print(f"  {t}")
    print("=" * W + "\n")


def say(t: str = "", indent: int = 2) -> None:
    for line in textwrap.wrap(t, W - indent) or [""]:
        print(" " * indent + line)


def kv(k: str, v) -> None:
    say(f"{k:<22} {v}")


def pause(interactive: bool) -> None:
    if interactive:
        try:
            input("\n  [Enter] next segment ")
        except EOFError:
            print()


# ---------------------------------------------------------------- lookups

def conn():
    return db.connect(readonly=True)


def scenario_mail(c, sid: str) -> str | None:
    sent = load_sim()["sent"].get(sid)
    if not sent:
        return None
    for r in db.rows(c, "SELECT entity, sources FROM events WHERE kind = 'mail.received'"):
        if any(str(s).startswith(f"mailbox:{sent['file']}") for s in r["sources"]):
            return r["entity"]
    return None


def children(c, parent: str, rel: str, prefix: str) -> list[str]:
    return [r["src"] for r in db.rows(c, "SELECT DISTINCT src FROM doc_links WHERE rel = ? AND dst = ? AND src LIKE ?"
                                         " ORDER BY seq", [rel, parent, prefix + ":%"])]


def last(c, entity: str, *kinds: str) -> dict | None:
    marks = ",".join("?" * len(kinds))
    rows = db.rows(c, f"SELECT seq, ts, actor, kind, payload, sources FROM events WHERE entity = ? AND kind IN ({marks})"
                      " ORDER BY seq DESC LIMIT 1", [entity, *kinds])
    return rows[0] if rows else None


def s04_s08_status(c) -> dict:
    """Has this run shown the learning loop yet?"""
    out = {"s04_rejected": False, "revision_adopted": False, "s08_caught": False}
    q = None
    mail = scenario_mail(c, "S04")
    if mail:
        qs = children(c, mail, "from_mail", "quote")
        q = qs[-1] if qs else None
    so = (children(c, q, "from_quote", "so") or [None])[0] if q else None
    if so and last(c, so, "approval.rejected"):
        out["s04_rejected"] = True
    out["revision_adopted"] = bool(db.rows(c, "SELECT 1 FROM events e WHERE e.kind = 'workflow.adopted' AND e.entity = 'workflow:orders'"
                                              " AND EXISTS (SELECT 1 FROM doc_links l WHERE l.seq = e.seq AND l.rel = 'addresses')"))
    mail8 = scenario_mail(c, "S08")
    if mail8:
        q8 = (children(c, mail8, "from_mail", "quote") or [None])[-1]
        so8 = (children(c, q8, "from_quote", "so") or [None])[0] if q8 else None
        if so8:
            first = db.rows(c, "SELECT payload FROM events WHERE entity = ? AND kind = 'so.drafted' ORDER BY seq LIMIT 1", [so8])
            lines = (first[0]["payload"].get("lines") if first else []) or []
            out["s08_caught"] = any(abs(float(l.get("unit_price", 0)) - 4.50) < 0.005 for l in lines) \
                and not last(c, so8, "approval.rejected")
    return out


# ---------------------------------------------------------------- segments

def moment(c) -> None:
    title("THE MOMENT  -  a price conflict nobody asked it to look for  (scenario S03)")
    mail = scenario_mail(c, "S03")
    if not mail:
        say("S03 has not been received in this run yet.")
        return
    m = last(c, mail, "mail.received")["payload"]
    kv("From", m["from"])
    kv("Subject", m["subject"])
    say("")
    say("Email body says:")
    say(m["body_excerpt"].strip(), indent=6)
    att = (m.get("attachments") or [{}])[0]
    if att.get("text_path") and Path(att["text_path"]).exists():
        pages = json.loads(Path(att["text_path"]).read_text(encoding="utf-8"))
        say("")
        say(f"Attached PDF ({att['name']}), page 1 says:")
        lines = pages[0]["text"].splitlines()
        shown = [l for l in lines if "$" in l or "total" in l.lower()] or lines[:6]
        say(lines[0], indent=6)
        for line in shown[:4]:
            say(line, indent=6)
    q = (children(c, mail, "from_mail", "quote") or [None])[-1]
    conflict = last(c, q, "quote.conflict") if q else None
    if conflict:
        p = conflict["payload"]
        say("")
        say("The desk's intake agent recorded a CONFLICT instead of guessing:")
        rec = p.get("recommendation") or {}
        why = rec.get("why") if isinstance(rec, dict) else None
        for src, cand in (p.get("candidates") or {}).items():
            for ln in (cand.get("lines") or [])[:2] if isinstance(cand, dict) else []:
                say(f"{src:<10} {ln.get('qty')} x {ln.get('item')} at ${float(ln.get('unit_price', 0)):.2f}"
                    f"   (source: {ln.get('source')})", indent=6)
        if why or p.get("why"):
            say("")
            say(f"Its recommendation: {why or p.get('why')}", indent=6)
    clar = last(c, q, "quote.clarified") if q else None
    appr = db.rows(c, "SELECT actor, payload FROM events WHERE entity = ? AND kind = 'approval.granted' ORDER BY seq DESC LIMIT 1", [q]) if q else []
    if appr:
        say("")
        say(f"Controller decision ({appr[0]['actor']}): {appr[0]['payload'].get('note')}")
        say("(in this benchmark the controller is a stand-in agent answering from an answer key)", indent=4)
    if clar:
        say(f"Resolved quote recorded at seq {clar['seq']}; the sales order is priced from the PDF.")


def what(c) -> None:
    title("WHAT IT IS")
    say("An autonomous revenue desk for Halyard Marine Supply (a simulated company). Five agents take a")
    say("customer quote to cash in real Odoo books and a real sandbox bank: intake -> orders -> billing")
    say("-> cash, with reconcile checking all three. Each agent works inside a WidgeX workflow that")
    say("code-checks every step. A controller keeps only the decisions. Every action is appended to one")
    say("tamper-evident ledger.")
    say("")
    say(f"Diagram: open {ROOT / 'docs' / 'architecture.html'} in a browser.")


def gate(c) -> None:
    title("THE GATE  -  nothing reaches the books without an approval")
    tmp = Path(tempfile.mkdtemp()) / "demo-ledger.db"
    os.environ["LEDGER_DB"] = str(tmp)
    t = db.connect(tmp)
    db.init(t)
    db.append(t, actor="orders-agent", role="orders", kind="so.drafted", entity="so:SO-DEMO",
              payload={"customer": "Bay Rigging Co", "total": 2000.0,
                       "lines": [{"item": "Galvanized Shackle 1/2in", "qty": 400, "unit_price": 5.0}]})
    say("1. An agent drafts a sales order (in a scratch ledger, so nothing real changes).")
    say("2. It tries to confirm it in Odoo without the controller's approval:")
    say("")
    from connectors import odoo
    try:
        odoo.post(t, odoo.Odoo(), "so:SO-DEMO", {"actor": "orders-agent", "role": "orders", "run_id": None,
                                                 "workflow": "orders", "workflow_version": None})
        say("   (unexpected: it posted)")
    except db.LedgerError as e:
        say(f"   REFUSED by code, before Odoo was called:  {e}", indent=4)
    say("")
    say("3. The agent tries to approve its own order:")
    try:
        db.append(t, actor="orders-agent", role="controller", on_behalf_of="orders-agent", kind="approval.granted",
                  entity="so:SO-DEMO")
    except db.LedgerError as e:
        say(f"   REFUSED:  {e}", indent=4)
    del os.environ["LEDGER_DB"]
    say("")
    mail = scenario_mail(c, "S03")
    q = (children(c, mail, "from_mail", "quote") or [None])[-1] if mail else None
    so = (children(c, q, "from_quote", "so") or [None])[0] if q else None
    if so:
        say(f"4. In the live run, every number traces back to its source. `python -m ledgerdb trace {so}`:")
        say("")
        tr = db.trace(c, so)
        for e in tr["events"]:
            if e["kind"] in ("mail.received", "quote.conflict", "quote.clarified", "approval.granted", "so.drafted",
                             "erp.posted", "invoice.drafted", "payment.applied", "je.posted"):
                src = ", ".join(str(s) for s in e["sources"][:2])
                say(f"seq {e['seq']:>4}  {e['kind']:<17} {e['entity']:<24} by {e['actor']:<20} {src}"[:W - 4], indent=4)


def learn(c) -> None:
    title("SELF-CORRECTION  -  the desk rewrites its own workflow after being overruled  (S04 -> S08)")
    st = s04_s08_status(c)
    f = db.rows(c, "SELECT entity, payload FROM events WHERE kind = 'feedback.recorded' AND json_extract(payload,'$.driver') = 'orders'"
                   " ORDER BY seq")
    if f:
        say(f"1. The controller rejected a sales order. Feedback recorded ({f[0]['entity']}):")
        say(f"\"{f[0]['payload'].get('note')}\"", indent=6)
    else:
        say("1. (S04 has not been rejected yet in this run.)")
    prop = db.rows(c, "SELECT payload FROM events WHERE kind = 'workflow.proposed' AND entity = 'workflow:orders' ORDER BY seq DESC LIMIT 1")
    if prop:
        p = prop[0]["payload"]
        say("")
        say("2. The orders agent diagnosed its own workflow and proposed a revision:")
        say(str(p.get("diagnosis") or p.get("summary"))[:500], indent=6)
    ad = db.rows(c, "SELECT payload FROM events e WHERE kind = 'workflow.adopted' AND entity = 'workflow:orders'"
                    " AND EXISTS (SELECT 1 FROM doc_links l WHERE l.seq = e.seq AND l.rel = 'addresses') ORDER BY seq DESC LIMIT 1")
    if ad:
        a = ad[0]["payload"]
        say("")
        say(f"3. Adopted after review: orders v{a.get('previous')} -> v{a.get('version')}; old version archived at "
            f"workflows/history/orders/v{a.get('previous')}/. The change:")
        diff = [l for l in (a.get("diff") or "").splitlines() if l.startswith("+") and not l.startswith("+++")][:8]
        for l in diff:
            say(l[:W - 8], indent=6)
    say("")
    if st["s08_caught"]:
        say("4. S08: the same customer again at list price. This time the desk priced it at the contract price")
        say("   on its first draft, with no controller correction needed.")
    else:
        say("4. S08 (the repeat, caught without the controller) has not happened yet in this run.")


def proof(c) -> None:
    title("PROOF  -  scored against a planted answer key, and what is real vs simulated")
    from eval.score import run as score
    out = score(db.connect())
    t = out["tally"]
    say(f"Checks: {t.get('pass', 0)} pass, {t.get('fail', 0)} fail, {t.get('pending', 0)} pending.   "
        f"Ledger hash chain verified: {out['ledger_verified']}")
    say("")
    for s in out["scenarios"]:
        st = [ch["status"] for ch in s["checks"]]
        mark = "FAIL" if "fail" in st else ("pass" if st and all(x == "pass" for x in st) else "pending")
        say(f"{s['id']}  {mark:<8} {s['title']}"[:W - 4], indent=4)
    say("")
    say("Real: Odoo 18 books (local), Increase sandbox bank API, the agents' reading of emails and PDFs.")
    say("Simulated: the company, its customers and suppliers, the emails, the payments, and in this")
    say("benchmark the controller (a stand-in agent answering from an answer key).")


def phish(c) -> None:
    title("OPTIONAL  -  the email that tried to redirect a payment  (S09)")
    mail = scenario_mail(c, "S09")
    if mail:
        m = last(c, mail, "mail.received")["payload"]
        say("The email said:")
        say(m["body_excerpt"].strip()[:420], indent=6)
        sus = db.rows(c, "SELECT payload FROM events WHERE kind = 'note.suspicious' ORDER BY seq DESC LIMIT 1")
        if sus:
            say("")
            say("Intake flagged it as suspicious and did not follow it.")
    say("")
    say("Even if an agent tried to pay that account, the bank connector checks Odoo's vendor master first:")
    from connectors import increase
    problem = increase.destination_problem("Harbor Forge", "026009593", "000123456")
    say(f"REFUSED:  {problem}", indent=6)


def close(c) -> None:
    title("THE SEATS IT FILLS")
    say("Order entry, customer billing, vendor bills with 3-way match, cash application, vendor payments,")
    say("and reconciliation, with the controller keeping only the decisions.")


def check() -> int:
    c = conn()
    st = s04_s08_status(c)
    say("Segments ready now: moment, what, gate, proof, phish, close.")
    say(f"learn (S04 -> S08):  S04 rejected: {st['s04_rejected']}   revision adopted: {st['revision_adopted']}   "
        f"S08 caught without the controller: {st['s08_caught']}")
    say("learn is ready to record." if all(st.values()) else "learn is NOT ready yet: wait for the current run to reach S08.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout,):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--check":
        return check()
    c = conn()
    chosen = [a for a in args if a in SEGMENTS] or list(SEGMENTS)
    interactive = len(chosen) > 1 and sys.stdin.isatty()
    fns = {"moment": moment, "what": what, "gate": gate, "learn": learn, "proof": proof, "phish": phish, "close": close}
    for i, name in enumerate(chosen):
        fns[name](c)
        if i < len(chosen) - 1:
            pause(interactive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
