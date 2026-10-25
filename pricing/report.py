"""The price recommendation finance receives: an HTML email with one chart per part, the full table, the
judgement calls and their evidence, and exactly how to reply.

    render(recommendation) -> {"html", "text", "subject", "charts": [paths], "dir"}

The charts follow the dataviz skill's rules:
- One axis per chart. Price and win probability are two stacked panels sharing the quantity axis, never a
  dual axis.
- Three series (the first three palette slots, validated all-pairs) with a legend and direct end labels.
- Thin 2px lines and >=8px markers with a surface ring. Hairline solid grid. Text in ink colours, never series
  colours.

An email can't carry a hover layer, so the table under each chart is the accessible view of every value.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from ledgerdb import db

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = {"recommended": "#2a78d6", "neutral": "#eb6834", "cost": "#1baf7a"}  # palette slots 1-3
FONT = ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"]
CALL_WORDS = {"growth": "Account growth", "wtp": "Willingness to pay", "urgency": "Urgency"}


def _money(x: float | None, digits: int | None = None) -> str:
    if x is None:
        return "-"
    if digits is None:
        digits = 4 if abs(x) < 1 else 2
    return f"${x:,.{digits}f}"


def _qty(q: float) -> str:
    q = int(q)
    return f"{q // 1000}K" if q >= 1000 and q % 1000 == 0 else f"{q:,}"


def chart(line: dict, path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator, PercentFormatter

    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": FONT, "font.size": 10,
                         "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED,
                         "ytick.color": MUTED, "text.color": INK})
    sched = [r for r in line["schedule"] if r.get("unit_price") is not None]
    neutral = [r for r in line.get("neutral", []) if r.get("unit_price") is not None]
    q = [r["qty"] for r in sched]
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(7.6, 5.4), dpi=200,
                                   gridspec_kw={"height_ratios": [3, 2], "hspace": 0.12})
    fig.patch.set_facecolor(SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        ax.grid(True, axis="y", color=GRID, linewidth=0.6, linestyle="-")
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(AXIS)
        ax.spines["bottom"].set_color(AXIS)
        ax.set_xscale("log")
    # Panel 1: unit price.
    if all("band" in r for r in sched):
        ax1.fill_between(q, [r["band"][0] for r in sched], [r["band"][1] for r in sched],
                         color=SERIES["recommended"], alpha=0.10, linewidth=0, label="Model uncertainty (80%)")
    ax1.plot(q, [r["unit_cost"] for r in sched], color=SERIES["cost"], lw=2, solid_capstyle="round",
             label="Our cost (best source)")
    if neutral:
        ax1.plot([r["qty"] for r in neutral], [r["unit_price"] for r in neutral], color=SERIES["neutral"], lw=2,
                 dashes=(4, 3), label="If the calls were neutral")
    ax1.plot(q, [r["unit_price"] for r in sched], color=SERIES["recommended"], lw=2, solid_joinstyle="round",
             marker="o", ms=6, mec=SURFACE, mew=2, label="Recommended price", zorder=3)
    req = line["qty"]
    for ax in (ax1, ax2):
        ax.axvline(req, color=AXIS, lw=1)
    ax2.annotate(f"requested {_qty(req)}", (req, 1), xycoords=("data", "axes fraction"), xytext=(4, -12),
                 textcoords="offset points", color=INK2, fontsize=9)
    # Direct end labels (text in ink, the coloured line beside it carries identity).
    short = lambda v: _money(v, 2 if v >= 1 else 3)
    last = sched[-1]
    ax1.annotate(short(last["unit_price"]), (q[-1], last["unit_price"]), xytext=(6, 4), textcoords="offset points",
                 color=INK, fontsize=9)
    ax1.annotate(f"cost {short(last['unit_cost'])}", (q[-1], last["unit_cost"]), xytext=(6, -10),
                 textcoords="offset points", color=INK2, fontsize=9)
    ax1.set_ylabel("Unit price (USD)")
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: short(v)))
    # Title, then the legend in one row beneath it, both above the plot so neither covers data.
    ax1.set_title(f"{line['mpn']}: recommended price breaks", loc="left", color=INK, fontsize=12, pad=30)
    ax1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4, frameon=False, fontsize=8.5, labelcolor=INK2,
               handlelength=1.8, columnspacing=1.2, borderaxespad=0.2)
    # Panel 2: chance of winning at the recommended price (one series: no legend; the title names it).
    ax2.plot(q, [r["p_win"] for r in sched], color=SERIES["recommended"], lw=2, marker="o", ms=6, mec=SURFACE, mew=2)
    ax2.set_ylim(0, 1)
    ax2.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax2.set_ylabel("Chance they accept")
    ax2.set_xlabel("Quantity (units, log scale)")
    ax2.xaxis.set_major_locator(FixedLocator(q))
    ax2.xaxis.set_minor_locator(NullLocator())
    ax2.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _qty(v)))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


def _esc(x: Any) -> str:
    return html.escape(str(x))


def render(rec: dict, out_dir: Path | None = None) -> dict[str, Any]:
    from connectors.mailer import finance_approvers
    finance = (finance_approvers() or ["finance"])[0]
    rfq = rec["rfq"]
    safe = rfq.replace(":", "-")
    out_dir = out_dir or db.desk_home() / "pricing" / "reports" / f"{safe}-{rec.get('seq', 'draft')}"
    charts = []
    for i, line in enumerate(rec["lines"]):
        if line.get("schedule") and any(r.get("unit_price") for r in line["schedule"]) and "neutral" in line:
            charts.append(chart(line, out_dir / f"chart-{i + 1}.png"))
        else:
            charts.append(None)
    customer = rec.get("customer") or "the customer"
    subject = (f"Price recommendation: {customer}, {rfq} "
               f"({len(rec['lines'])} part{'s' if len(rec['lines']) != 1 else ''}, "
               f"{_money(rec.get('requested_total'), 2)} at the requested quantities)")
    p = []
    p.append(f"<div style=\"font-family:{','.join(FONT)},sans-serif;color:{INK};max-width:820px;background:#fff\">")
    p.append(f"<h2 style=\"margin:0 0 4px\">Price recommendation: {_esc(customer)}</h2>")
    p.append(f"<p style=\"color:{INK2};margin:0 0 16px\">{_esc(rfq)} &middot; recommendation #{_esc(rec.get('seq'))} "
             f"&middot; {_money(rec.get('requested_total'), 2)} at the requested quantities</p>")
    # Judgement calls.
    p.append("<h3 style=\"margin:16px 0 6px\">What the desk judged about this customer</h3>")
    p.append(f"<table cellpadding=\"6\" style=\"border-collapse:collapse;font-size:14px\">")
    calls = rec["calls"]
    ev = rec.get("evidence", {})
    for k, words in CALL_WORDS.items():
        cites = "<br>".join(f"&ldquo;{_esc(e['says'])}&rdquo; <span style=\"color:{MUTED}\">({_esc(e['source'])})</span>"
                            for e in ev.get(k, []))
        p.append(f"<tr style=\"border-top:1px solid {GRID}\"><td><b>{words}</b></td><td>{_esc(calls[k])}</td>"
                 f"<td style=\"color:{INK2}\">{cites}</td></tr>")
    mg = rec.get("measured_growth", {})
    p.append("</table>")
    note = (f"Measured from our books: {_money(mg.get('trailing_12m'), 0)} ordered in the last 12 months against "
            f"{_money(mg.get('prior_12m'), 0)} the year before ({mg.get('change', 0):+.0%}), which reads as "
            f"<b>{_esc(mg.get('label'))}</b>.")
    if rec.get("growth_disagrees"):
        note += f" <b>The desk's growth call disagrees with this.</b> Its evidence is above."
    p.append(f"<p style=\"color:{INK2};font-size:13px\">{note} Repeat customer: {'yes' if rec.get('repeat') else 'no'}.</p>")
    # Lines.
    for i, line in enumerate(rec["lines"]):
        p.append(f"<h3 style=\"margin:22px 0 6px\">{_esc(line['mpn'])}: {_qty(line['qty'])} requested"
                 + (f", needed by {_esc(line['need_by'])}" if line.get("need_by") else "") + "</h3>")
        if line.get("contract_price") is not None:
            p.append(f"<p>Contract price on file: <b>{_money(line['contract_price'])}</b>. Not modelled.</p>")
        if charts[i]:
            p.append(f"<img src=\"cid:chart-{i + 1}\" width=\"760\" alt=\"Recommended price breaks for "
                     f"{_esc(line['mpn'])}; every value is in the table below\" style=\"display:block;max-width:100%\">")
        p.append(f"<table cellpadding=\"5\" style=\"border-collapse:collapse;font-size:13px;"
                 f"font-variant-numeric:tabular-nums;margin-top:6px\">")
        p.append(f"<tr style=\"color:{INK2};text-align:right\"><th style=\"text-align:right\">Qty</th><th>Unit price</th>"
                 f"<th>Range (80%)</th><th>If neutral</th><th>Our cost</th><th>Margin</th><th>Chance they accept</th>"
                 f"<th>Expected profit</th><th style=\"text-align:left\">Source</th></tr>")
        neutral = {r["qty"]: r for r in line.get("neutral", [])}
        for r in line["schedule"]:
            bold = "font-weight:600;" if r["qty"] == line["qty"] else ""
            band = r.get("band")
            src = (f"{_esc(r.get('vendor'))}, {r.get('lead_days')}d" if r.get("vendor") else "no source")
            if r.get("authorized") is False:
                src += " <b>(broker)</b>"
            flags = "".join(f"<div style=\"color:{INK2}\">&#9888; {_esc(f)}</div>" for f in r.get("flags", []))
            p.append(f"<tr style=\"border-top:1px solid {GRID};text-align:right;{bold}\"><td>{_qty(r['qty'])}</td>"
                     f"<td>{_money(r.get('unit_price'))}</td>"
                     f"<td>{(_money(band[0]) + '&ndash;' + _money(band[1])) if band else '-'}</td>"
                     f"<td>{_money(neutral.get(r['qty'], {}).get('unit_price'))}</td>"
                     f"<td>{_money(r.get('unit_cost'))}</td>"
                     f"<td>{('%.0f%%' % (100 * r['margin'])) if r.get('margin') is not None else '-'}</td>"
                     f"<td>{('%.0f%%' % (100 * r['p_win'])) if r.get('p_win') is not None else '-'}</td>"
                     f"<td>{_money(r.get('expected_profit'), 0) if r.get('expected_profit') is not None else '-'}</td>"
                     f"<td style=\"text-align:left\">{src}{flags}</td></tr>")
        p.append("</table>")
    # How it was made, and how to answer.
    m = rec.get("model", {})
    p.append(f"<p style=\"color:{INK2};font-size:12px;margin-top:18px\">How these prices were set: a win-probability "
             f"model fit on {m.get('n', '?')} past quotes (AUC {m.get('metrics', {}).get('auc', '?')}). For each "
             f"quantity it picks the price with the highest expected profit (chance they accept &times; quantity "
             f"&times; margin, less a {_money(rec.get('quote_cost'), 0)} quoting cost). It then makes the schedule a "
             f"bulk curve (never dearer at a larger quantity) with at least a {rec.get('margin_floor', 0):.0%} "
             f"margin. The desk made the three judgement calls; the model decided how much each one moves the "
             f"price. Our quote history in this benchmark is simulated. Inputs {_esc(rec.get('inputs_sha256', '')[:12])}.</p>")
    p.append(f"<div style=\"border:1px solid {GRID};padding:10px 14px;margin-top:12px;font-size:14px\">"
             f"<b>To decide, reply to this email</b> with one of these as the first line:"
             f"<pre style=\"margin:8px 0\">APPROVE\n\nAPPROVE WITH CHANGES\n{_esc(rec['lines'][0]['mpn'])} "
             f"{_qty(rec['lines'][0]['qty']).replace('K', '000')}: 1.23\n\nREJECT: &lt;why&gt;</pre>"
             f"With APPROVE WITH CHANGES, each line is <code>MPN quantity: unit price</code>; any tier you don't list "
             f"keeps the recommended price. Only replies from {_esc(finance)} are acted on.</div>")
    p.append("</div>")
    html_doc = "\n".join(p)
    text = [subject, ""]
    for k, words in CALL_WORDS.items():
        text.append(f"{words}: {calls[k]}")
    for line in rec["lines"]:
        text.append("")
        text.append(f"{line['mpn']} ({line['qty']} requested)")
        for r in line["schedule"]:
            if r.get("unit_price") is not None:
                text.append(f"  {r['qty']:>6}  {_money(r['unit_price']):>10}  margin {100 * r['margin']:.0f}%  "
                            f"accept {100 * r['p_win']:.0f}%  {r.get('vendor')}")
    text += ["", "Reply APPROVE, APPROVE WITH CHANGES (one 'MPN qty: price' per line), or REJECT: <why>."]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.html").write_text(html_doc, encoding="utf-8")
    # The same report for a browser: the email's inline images (cid:) point at the chart files beside it.
    view = html_doc
    for i, c in enumerate(charts):
        if c:
            view = view.replace(f"cid:chart-{i + 1}", Path(c).name)
    (out_dir / "report-view.html").write_text(view, encoding="utf-8")
    (out_dir / "recommendation.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    return {"dir": str(out_dir), "html": str(out_dir / "report.html"), "subject": subject, "text": "\n".join(text),
            "charts": [str(c) if c else None for c in charts]}
