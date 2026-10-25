"""Live (needs the Redwood Odoo database): one RFQ through the real simulator, finance stand-in, mailer and scorer,
with scripted stand-ins for the agents. A dress rehearsal for the benchmark run."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = r'''

import json, os, tempfile, sys
home = tempfile.mkdtemp(prefix="redwood-rehearsal-")
os.environ.update({"REVENUE_DESK_HOME": home, "DESK_WORLD": "redwood", "DESK_STRUCTURE": "structure/redwood",
                   "ODOO_DB": "redwood", "ODOO_URL": "http://127.0.0.1:8069"})
from ledgerdb import db
conn = db.connect(); db.init(conn)
from seed import redwood_history, sim
from pricing import model as M, send as S
from connectors import inbox
from desk import finance_reply
redwood_history.write(redwood_history.generate())
print("sent", [x["scenario"] for x in sim.send_quotes(conn, 1)])
new = inbox.pull_drop(conn, {"actor": "forwarder", "role": "intake", "workflow": "forwarder"})
u01 = next(n for n in new if "CI-2291" in n["subject"])
I = {"actor": "intake-agent", "role": "intake", "workflow": "intake"}
db.append(conn, kind="mail.classified", entity=u01["entity"], payload={"class": "rfq"}, **I)
db.append(conn, kind="quote.received", entity="quote:Q-1001", payload={"customer": "Cobalt Instruments"},
          links=[{"rel": "from_mail", "to": u01["entity"]}], **I)
from datetime import date, timedelta
nb = (date.today() + timedelta(days=30)).isoformat()
db.append(conn, kind="quote.extracted", entity="quote:Q-1001", payload={"customer": "Cobalt Instruments", "rfq": True, "total": None,
          "need_by": nb, "lines": [{"mpn": "TPS62130RGTR", "item": "TPS62130RGTR", "qty": 500, "source": "x"},
                                   {"mpn": "W25Q128JVSIQ", "item": "W25Q128JVSIQ", "qty": 500, "source": "x"}]}, **I)
P = {"actor": "pricing-agent", "role": "pricing", "workflow": "pricing"}
calls = {k: {"value": v, "evidence": [{"source": "gmail:x#body", "says": "standard lead time is fine"}]} for k, v in
         {"growth": "flat", "wtp": "normal", "urgency": "normal"}.items()}
db.append(conn, kind="pricing.assessment", entity="quote:Q-1001", payload={"calls": calls}, **P)
rec = M.recommend(conn, "quote:Q-1001", P)
print("recommended", [(l["mpn"], l["requested"]["unit_price"], l["requested"]["vendor"]) for l in rec["lines"]])
print("report", S.send_report(conn, "quote:Q-1001", P)["mail"]["where"])
print("sim step 1", json.dumps(sim.step(conn).get("finance")))
inbox.pull_drop(conn, {"actor": "forwarder", "role": "intake", "workflow": "forwarder"})
print("finance reply", finance_reply.process(conn))
print("quote", S.send_quote(conn, "quote:Q-1001", {**P, "role": "quoting"}, note="Ships in 3 days.")["mail"]["where"])
print("sim step 2", json.dumps(sim.step(conn).get("customers")))
new = inbox.pull_drop(conn, {"actor": "forwarder", "role": "intake", "workflow": "forwarder"})
print("customer mail", [n["subject"] for n in new])
st = sim.load()
ans = st["answered"]["quote:Q-1001"]
if ans["taken"]:
    db.append(conn, kind="quote.accepted", entity="quote:Q-1001", payload={"customer": "Cobalt Instruments", "customer_po": ans["po"],
              "lines": [{"mpn": m, "item": m, "qty": n, "unit_price": p, "source": "x"} for m, n, p in ans["taken"]],
              "total": round(sum(n * p for _, n, p in ans["taken"]), 2)}, links=[{"rel": "from_mail", "to": new[0]["entity"]}], **I)
from eval import score
out = score.run(conn)
u = next(r for r in out["scenarios"] if r["id"] == "U01")
for c in u["checks"]: print(f"  {c['status']:8} {c['check']:45} {c['detail']}")
print("home", home)

'''


@pytest.mark.live
def test_u01_rehearsal():
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=300,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stderr[-2000:]
    checks = [l for l in out.stdout.splitlines() if l.startswith("  ")]
    assert checks and not any(l.strip().startswith("fail") for l in checks), out.stdout
