"""Send the two emails a priced RFQ needs. Code writes both: the agent never types a price into an email.

    python -m pricing.send report --rfq quote:Q-1001 --actor pricing-agent --role pricing
        Render the latest recommendation, email it to finance, and record approval.requested on the RFQ
        (review_state finance_review). Finance's reply is read by desk/finance_reply.py.

    python -m pricing.send quote --rfq quote:Q-1001 --actor quoting-agent --role quoting [--note "..."]
        Email the customer the price breaks finance approved, as text and a PDF, valid 30 days, and record
        quote.sent. Refused (connectors/mailer.py) unless finance approved the latest recommendation, and
        only to the customer's email on file in Odoo.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, timedelta
from typing import Any

from connectors import mailer
from ledgerdb import db
from . import report

VALID_DAYS = 30


def latest_recommendation(conn, rfq: str) -> dict:
    rows = db.rows(conn, "SELECT seq, payload FROM events WHERE entity = ? AND kind = 'pricing.recommended' "
                         "ORDER BY seq DESC LIMIT 1", [rfq])
    if not rows:
        raise SystemExit(f"{rfq} has no recommendation: run `python -m pricing.model recommend --rfq {rfq}` first")
    return {**rows[0]["payload"], "seq": rows[0]["seq"]}


def send_report(conn, rfq: str, ctx: dict) -> dict[str, Any]:
    rec = latest_recommendation(conn, rfq)
    out = report.render(rec)
    to = (mailer.finance_approvers() or [None])[0]
    if not to:
        raise SystemExit("policy.finance_approvers is empty: there is nobody to send the report to")
    images = [(f"chart-{i + 1}", c) for i, c in enumerate(out["charts"]) if c]
    html = open(out["html"], encoding="utf-8").read()
    sent = mailer.send(conn, purpose="pricing_report", to=to, subject=out["subject"], text=out["text"], html=html,
                       images=images, rfq=rfq, customer=rec.get("customer"), ctx=ctx,
                       links=[("reports", rfq)], payload_extra={"recommendation_seq": rec["seq"],
                                                               "report_dir": out["dir"]})
    summary = (f"{rec.get('customer')}: {len(rec['lines'])} line(s), {report._money(rec.get('requested_total'), 2)} "
               f"at the requested quantities. Calls: growth {rec['calls']['growth']}, willingness to pay "
               f"{rec['calls']['wtp']}, urgency {rec['calls']['urgency']}. Report emailed to {to}.")
    req = db.append(conn, kind="approval.requested", entity=rfq,
                    payload={"review_state": "finance_review", "summary": summary, "items": [rfq],
                             "recommendation_seq": rec["seq"], "report_mail": sent["entity"],
                             "report_message_id": sent["message_id"],
                             "recommendation": "Approve the recommended price breaks, change any tier, or reject."},
                    sources=[sent["where"]], **ctx)
    return {"report": out["html"], "mail": sent, "approval_requested_seq": req["seq"]}


def approved_lines(conn, rfq: str) -> tuple[dict, list[dict]]:
    appr = mailer.approved_price(conn, rfq)
    if appr is None:
        raise SystemExit(f"{rfq} has no finance approval after its latest recommendation")
    rec = latest_recommendation(conn, rfq)
    lines = appr.get("approved") or []
    if not lines:  # a plain APPROVE: the recommendation as it stands
        lines = [{"mpn": ln["mpn"], "qty": ln["qty"], "lead_days": (ln.get("requested") or {}).get("lead_days"),
                  "schedule": [{"qty": r["qty"], "unit_price": r["unit_price"], "lead_days": r.get("lead_days")}
                               for r in ln["schedule"] if r.get("unit_price") is not None]}
                 for ln in rec["lines"]]
    return {**appr, "recommendation": rec}, lines


def _pdf(customer: str, rfq: str, lines: list[dict], valid_until: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from seed import world
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, y, f"{world.COMPANY['name']}: Quotation")
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(60, y, f"For {customer}   Ref {rfq}   Valid until {valid_until}")
    y -= 26
    for ln in lines:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(60, y, f"{ln['mpn']}  (you asked for {ln['qty']:,})")
        y -= 16
        c.setFont("Helvetica-Bold", 9)
        for x, h in ((80, "Quantity"), (180, "Unit price"), (280, "Lead time")):
            c.drawString(x, y, h)
        y -= 13
        c.setFont("Helvetica", 9)
        for r in ln["schedule"]:
            c.drawString(80, y, f"{int(r['qty']):,}+")
            c.drawString(180, y, f"${r['unit_price']:,.4f}" if r["unit_price"] < 1 else f"${r['unit_price']:,.2f}")
            c.drawString(280, y, f"{r['lead_days']} days" if r.get("lead_days") is not None else "")
            y -= 12
            if y < 80:
                c.showPage()
                y = 740
        y -= 10
    c.setFont("Helvetica", 8)
    c.drawString(60, 60, "Prices in USD, ex works San Jose. California sales tax added where it applies. "
                         "Reply with your PO to order.")
    c.save()
    return buf.getvalue()


def send_quote(conn, rfq: str, ctx: dict, note: str = "", odoo=None) -> dict[str, Any]:
    import re
    if re.search(r"\$|\d+\.\d\d|\bUSD\b", note or ""):
        raise mailer.MailRefused("the cover note can't contain prices: the prices come only from finance's approval")
    appr, lines = approved_lines(conn, rfq)
    customer = appr["recommendation"].get("customer")
    if odoo is None:
        from connectors.odoo import Odoo
        odoo = Odoo()
    to = mailer.customer_email_on_file(customer, odoo)
    valid_until = (date.today() + timedelta(days=VALID_DAYS)).isoformat()
    original = db.rows(conn, "SELECT payload FROM events WHERE entity IN (SELECT dst FROM doc_links WHERE src = ? "
                             "AND rel = 'from_mail') AND kind = 'mail.received' LIMIT 1", [rfq])
    reply_to = original[0]["payload"].get("message_id_header") if original else None
    subject = (f"Re: {original[0]['payload'].get('subject')}" if original else f"Quotation {rfq}")
    text = [f"Hello,", "", (note.strip() + "\n") if note.strip() else "",
            f"Thank you for your request. Our prices, valid until {valid_until}:", ""]
    for ln in lines:
        text.append(f"{ln['mpn']} (you asked for {ln['qty']:,}):")
        for r in ln["schedule"]:
            price = f"${r['unit_price']:,.4f}" if r["unit_price"] < 1 else f"${r['unit_price']:,.2f}"
            lead = f", {r['lead_days']} days" if r.get("lead_days") is not None else ""
            text.append(f"  {int(r['qty']):>7,}+  {price}{lead}")
        text.append("")
    text += ["Reply with your purchase order to go ahead.", "", "Redwood Micro Components"]
    pdf = _pdf(customer, rfq, lines, valid_until)
    sent = mailer.send(conn, purpose="customer_quote", to=to, subject=subject, text="\n".join(text), rfq=rfq,
                       customer=customer, ctx=ctx, attachments=[(f"Redwood-quote-{rfq.split(':')[-1]}.pdf", pdf)],
                       in_reply_to=reply_to, odoo=odoo, payload_extra={"approval_seq": appr["seq"]})
    ev = db.append(conn, kind="quote.sent", entity=rfq,
                   payload={"customer": customer, "to": to, "valid_until": valid_until, "lines": lines,
                            "approval_seq": appr["seq"], "approved_by": appr["actor"], "mail": sent["entity"],
                            "message_id": sent["message_id"]},
                   sources=[sent["where"]], links=[{"rel": "sent_as", "to": sent["entity"]}], **ctx)
    return {"quote_sent_seq": ev["seq"], "mail": sent, "valid_until": valid_until}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pricing.send", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    qp = sub.add_parser("quoted", help="the approved price the customer was quoted for a part at a quantity")
    qp.add_argument("--rfq", required=True)
    qp.add_argument("--mpn", required=True)
    qp.add_argument("--qty", type=float, required=True)
    for name in ("report", "quote"):
        sp = sub.add_parser(name)
        sp.add_argument("--rfq", required=True)
        sp.add_argument("--actor", required=True)
        sp.add_argument("--role", required=True)
        sp.add_argument("--workflow", default="pricing")
        if name == "quote":
            sp.add_argument("--note", default="", help="a short cover line for the customer (no prices)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    conn = db.connect()
    if args.cmd == "quoted":
        from checks.ledger_checks import approved_unit_price
        price = approved_unit_price(conn, args.rfq, args.mpn, args.qty)
        print(json.dumps({"rfq": args.rfq, "mpn": args.mpn, "qty": args.qty, "unit_price": price,
                          "problem": None if price is not None else "no quote.sent covers this part at this quantity"}))
        return 0 if price is not None else 1
    ctx = {"actor": args.actor, "role": args.role, "workflow": args.workflow}
    try:
        out = send_report(conn, args.rfq, ctx) if args.cmd == "report" else send_quote(conn, args.rfq, ctx, args.note)
    except mailer.MailRefused as e:
        print(json.dumps({"refused": str(e)}))
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
