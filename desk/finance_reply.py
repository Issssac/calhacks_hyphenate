"""Read finance's emailed decisions on price recommendations, in code, and record them.

    python -m desk.finance_reply            process every reply not yet handled (the forwarder runs this each pass)

A reply counts when all of these hold:
  * it answers a pricing report the desk sent: In-Reply-To or References matches a `mail.sent` with purpose
    pricing_report;
  * it comes from an address in policy.finance_approvers. With Gmail, DKIM must also pass for that address's
    domain. Mail from the local drop folder (the simulation) carries no signature, and the record says so;
  * its first line (quoted text ignored) is one of:
        APPROVE
        APPROVE WITH CHANGES      then one line per change:  <MPN> <qty>: <unit price>
        REJECT: <why>

Then it is written as approval.granted or approval.rejected on the RFQ, role finance, on behalf of
human:<address>, with the reply quoted and the approved schedule spelled out. Anything else is left for a person:
the desk records note.finance_unclear on the RFQ and does not guess.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import json
import re
import sys
from pathlib import Path
from typing import Any

from connectors import mailer
from ledgerdb import db

ACTOR = "finance-reply"
CHANGE = re.compile(r"^\s*(?P<mpn>[A-Za-z0-9][A-Za-z0-9\-/#.+]*)\s+(?P<qty>\d[\d,]*\s*[kK]?)\s*:\s*\$?\s*"
                    r"(?P<price>\d+(?:\.\d+)?)\s*$")


def _qty(s: str) -> int:
    s = s.replace(",", "").strip().lower()
    return int(float(s[:-1]) * 1000) if s.endswith("k") else int(s)


def _own_lines(body: str) -> list[str]:
    """The reply's own text: stop at the quoted original ("On ... wrote:" or lines starting with '>')."""
    out = []
    for ln in body.splitlines():
        if ln.lstrip().startswith(">") or re.match(r"^\s*On .+wrote:\s*$", ln) or ln.strip() == "-----Original Message-----":
            break
        out.append(ln.rstrip())
    while out and not out[0].strip():
        out.pop(0)
    return out


def parse(body: str) -> dict[str, Any]:
    lines = _own_lines(body)
    if not lines:
        return {"decision": None, "problem": "the reply is empty"}
    head = lines[0].strip()
    up = head.upper()
    if up.startswith("REJECT"):
        reason = head.split(":", 1)[1].strip() if ":" in head else " ".join(l.strip() for l in lines[1:]).strip()
        if not reason:
            return {"decision": None, "problem": "REJECT needs a reason after the colon"}
        return {"decision": "reject", "reason": reason}
    if up.rstrip(".! ") == "APPROVE":
        return {"decision": "approve", "changes": []}
    if up.startswith("APPROVE WITH CHANGES"):
        changes, bad = [], []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            m = CHANGE.match(ln)
            if m:
                changes.append({"mpn": m["mpn"], "qty": _qty(m["qty"]), "unit_price": float(m["price"])})
            elif changes or bad:  # stop at the first prose after the change lines
                break
            else:
                bad.append(ln.strip())
        if not changes:
            return {"decision": None, "problem": "APPROVE WITH CHANGES has no lines like `MPN qty: price`"
                                                 + (f" (could not read: {bad[0]!r})" if bad else "")}
        return {"decision": "approve_with_changes", "changes": changes}
    return {"decision": None, "problem": f"the first line {head[:60]!r} is not APPROVE, APPROVE WITH CHANGES or REJECT"}


def _identity(payload: dict, sender: str) -> tuple[bool, str]:
    """Whether the sender is an approver, and how far we can trust that it's them."""
    if sender not in mailer.finance_approvers():
        return False, f"{sender} is not in policy.finance_approvers"
    if payload.get("delivered_via") == "drop folder":
        return True, "asserted: local drop folder (simulation), no signature to check"
    raw_path = Path(payload.get("body_path", "")).parent / "message.eml"
    if raw_path.exists():
        msg = email.message_from_bytes(raw_path.read_bytes(), policy=email.policy.default)
        results = " ".join(str(h) for h in msg.get_all("Authentication-Results") or [])
        domain = sender.split("@", 1)[-1]
        if re.search(rf"dkim=pass[^;]*header\.(?:i|d)=@?{re.escape(domain)}", results):
            return True, f"DKIM pass for {domain}"
    return False, "no DKIM pass for the sender's domain"


def _apply_changes(rec: dict, changes: list[dict]) -> tuple[list[dict], list[str]]:
    lines, problems = [], []
    by_mpn = {ln["mpn"].upper(): ln for ln in rec["lines"]}
    for ch in changes:
        ln = by_mpn.get(ch["mpn"].upper())
        if not ln:
            problems.append(f"{ch['mpn']} is not on this RFQ")
        elif not any(r["qty"] == ch["qty"] for r in ln["schedule"]):
            problems.append(f"{ch['mpn']} has no {ch['qty']} tier (tiers: {', '.join(str(r['qty']) for r in ln['schedule'])})")
        elif ch["unit_price"] <= 0:
            problems.append(f"{ch['mpn']} {ch['qty']}: a price must be above zero")
    if problems:
        return [], problems
    for ln in rec["lines"]:
        sched = []
        for r in ln["schedule"]:
            if r.get("unit_price") is None:
                continue
            new = next((c["unit_price"] for c in changes
                        if c["mpn"].upper() == ln["mpn"].upper() and c["qty"] == r["qty"]), None)
            row = {"qty": r["qty"], "unit_price": new if new is not None else r["unit_price"],
                   "lead_days": r.get("lead_days"), "unit_cost": r.get("unit_cost"),
                   "changed_by_finance": new is not None, "recommended": r["unit_price"]}
            sched.append(row)
        lines.append({"mpn": ln["mpn"], "qty": ln["qty"], "schedule": sched})
    return lines, []


def process(conn) -> list[dict]:
    done = {e["payload"].get("reply") for e in db.rows(
        conn, "SELECT payload FROM events WHERE kind IN ('note.finance_processed', 'note.finance_unclear')")}
    out = []
    for m in db.rows(conn, "SELECT seq, entity, payload FROM events WHERE kind = 'mail.received' ORDER BY seq"):
        if m["entity"] in done:
            continue
        p = m["payload"]
        report = mailer.sent_for(conn, p.get("in_reply_to", ""))
        if not report or report.get("purpose") != "pricing_report":
            continue
        rfq = report["rfq"]
        sender = mailer._addr(p.get("from", ""))
        ctx_note = {"actor": ACTOR, "role": "finance", "on_behalf_of": f"human:{sender}"}
        body = Path(p["body_path"]).read_text(encoding="utf-8") if p.get("body_path") else p.get("body_excerpt", "")
        ok, identity = _identity(p, sender)
        parsed = parse(body) if ok else {"decision": None, "problem": identity}
        rec_rows = db.rows(conn, "SELECT seq, payload FROM events WHERE seq = ?", [report.get("recommendation_seq")])
        rec = rec_rows[0]["payload"] if rec_rows else None
        latest = conn.execute("SELECT MAX(seq) FROM events WHERE entity = ? AND kind = 'pricing.recommended'",
                              (rfq,)).fetchone()[0]
        if parsed["decision"] and rec_rows and rec_rows[0]["seq"] != latest:
            parsed = {"decision": None, "problem": "this reply answers an older recommendation; a newer one exists"}
        approved, problems = ([], [])
        if parsed["decision"] == "approve_with_changes" and rec:
            approved, problems = _apply_changes(rec, parsed["changes"])
            if problems:
                parsed = {"decision": None, "problem": "; ".join(problems)}
        if not parsed["decision"]:
            db.append(conn, kind="note.finance_unclear", entity=rfq, actor=ACTOR, role="tracker",
                      payload={"reply": m["entity"], "from": sender, "problem": parsed["problem"],
                               "reply_text": "\n".join(_own_lines(body))[:1500]},
                      sources=[f"ledger:seq{m['seq']}"])
            out.append({"rfq": rfq, "reply": m["entity"], "unclear": parsed["problem"]})
            continue
        own = "\n".join(_own_lines(body))[:2000]
        payload = {"decision": parsed["decision"], "via": "email reply", "identity": identity, "reply": m["entity"],
                   "reply_text": own, "recommendation_seq": report.get("recommendation_seq"),
                   "report_mail": report["entity"]}
        if parsed["decision"] == "reject":
            payload["note"] = parsed["reason"]
            kind = "approval.rejected"
        else:
            payload["note"] = "Approved as recommended." if parsed["decision"] == "approve" else \
                f"Approved with {len(parsed['changes'])} change(s)."
            payload["changes"] = parsed.get("changes", [])
            payload["approved"] = approved
            kind = "approval.granted"
        ev = db.append(conn, kind=kind, entity=rfq, payload=payload, sources=[f"ledger:seq{m['seq']}"],
                       links=[{"rel": "decides", "to": report["entity"]}], **ctx_note)
        db.append(conn, kind="note.finance_processed", entity=rfq, actor=ACTOR, role="tracker",
                  payload={"reply": m["entity"], "decision_seq": ev["seq"]})
        out.append({"rfq": rfq, "reply": m["entity"], "decision": parsed["decision"], "seq": ev["seq"]})
    return out


def main() -> int:
    conn = db.connect()
    sys.stdout.write(json.dumps(process(conn), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
