"""Increase sandbox bank: where customer cash actually lands and vendor payments leave.

    python -m connectors.increase setup                       one-time: account + account number
    python -m connectors.increase balance
    python -m connectors.increase track --actor tracker-1 --role tracker
    python -m connectors.increase simulate-inbound 450.00 --company "ACME CORP" --description "INV-1001" \
        --addenda "Payment for INV-1001 less damaged goods" --actor sim --role simulator
    python -m connectors.increase send billpayment:BP-1001 --actor cash-1 --role cash

`send` refuses unless the ledger's gate passes for the bill payment (it needs an
approval from someone other than whoever drafted it). Inbound transfers are
always simulated in the sandbox, and are written to the ledger as sim.* so the
evidence table can say so.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from ledgerdb import db
from .env import DATA_DIR, need

BANK_PATH = DATA_DIR / "bank.json"  # sandbox account ids, shared by every worktree


class BankError(RuntimeError):
    pass


class Increase:
    def __init__(self) -> None:
        self.key = need("INCREASE_API_KEY")["INCREASE_API_KEY"]
        env = os.environ.get("INCREASE_ENV", "sandbox")
        if env != "sandbox" and os.environ.get("INCREASE_ALLOW_PRODUCTION") != "yes":
            raise BankError("INCREASE_ENV is not 'sandbox': refusing to touch real money "
                            "unless INCREASE_ALLOW_PRODUCTION=yes is set deliberately")
        self.base = "https://sandbox.increase.com" if env == "sandbox" else "https://api.increase.com"
        self.session = requests.Session()

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:200]
        for attempt in range(4):
            r = self.session.request(method, self.base + path, params=params, json=body, headers=headers, timeout=60)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 1 + attempt)))
                continue
            break
        try:
            data = r.json()
        except ValueError:
            raise BankError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        if r.status_code >= 400:
            detail = {k: v for k, v in data.items() if k not in ("type", "status")}
            raise BankError(f"{method} {path}: {r.status_code} {data.get('type')}: {json.dumps(detail)[:800]}")
        return data

    def list_all(self, path: str, params: dict | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            q = {**(params or {}), "limit": 100}
            if cursor:
                q["cursor"] = cursor
            page = self.request("GET", path, q)
            out += page.get("data", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return out


def load_bank() -> dict:
    if not BANK_PATH.exists():
        raise BankError(f"no {BANK_PATH} yet: run `python -m connectors.increase setup`")
    return json.loads(BANK_PATH.read_text(encoding="utf-8"))


def _create_company(b: Increase) -> dict:
    """A fictional company to own the sandbox account. Sandbox data only; nothing here is real."""
    company = os.environ.get("COMPANY_NAME", "Halyard Marine Supply")
    return b.request("POST", "/entities", idempotency_key=f"entity-{company}", body={
        "structure": "corporation",
        "corporation": {
            "name": company,
            "legal_identifier": {"category": "us_employer_identification_number", "value": "000000000"},
            "address": {"line1": "1 Sandbox Pier", "city": "Alameda", "state": "CA", "zip": "94501",
                        "country": "US"},
            "incorporation_state": "CA",
            # Placeholder owner, sandbox only. Increase requires one.
            "beneficial_owners": [{
                "prongs": ["control"],
                "company_title": "Controller",
                "individual": {
                    "name": "Sandbox Owner",
                    "date_of_birth": "1980-01-01",
                    "address": {"line1": "1 Sandbox Pier", "city": "Alameda", "state": "CA", "zip": "94501",
                                "country": "US"},
                    "identification": {"method": "social_security_number", "number": "123456789"},
                },
            }],
        },
    })


def setup(b: Increase, name: str = "Revenue Desk Operating") -> dict:
    if BANK_PATH.exists():
        return load_bank()
    accounts = [a for a in b.list_all("/accounts") if a.get("status") == "open"]
    acct = next((a for a in accounts if a["name"] == name), None)
    if not acct:
        try:
            acct = b.request("POST", "/accounts", body={"name": name}, idempotency_key=f"acct-{name}")
        except BankError:
            # A fresh sandbox has no entity, and an account needs an owner.
            entities = [e for e in b.list_all("/entities") if e.get("status", "active") == "active"]
            entity = entities[0] if entities else _create_company(b)
            acct = b.request("POST", "/accounts", body={"name": name, "entity_id": entity["id"]},
                             idempotency_key=f"acct-{name}-{entity['id']}")
    numbers = b.list_all("/account_numbers", {"account_id": acct["id"]})
    num = numbers[0] if numbers else b.request(
        "POST", "/account_numbers", body={"account_id": acct["id"], "name": "Customer receipts"},
        idempotency_key=f"acctnum-{acct['id']}")
    bank = {"account_id": acct["id"], "account_name": acct["name"], "account_number_id": num["id"],
            "routing_number": num["routing_number"], "account_number": num["account_number"],
            "environment": "sandbox"}
    BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    BANK_PATH.write_text(json.dumps(bank, indent=2), encoding="utf-8")
    return bank


def balance(b: Increase) -> dict:
    bank = load_bank()
    try:
        bal = b.request("GET", f"/accounts/{bank['account_id']}/balance")
        return {"current": bal.get("current_balance", 0) / 100, "available": bal.get("available_balance", 0) / 100}
    except BankError:
        txns = b.list_all("/transactions", {"account_id": bank["account_id"]})
        return {"current": sum(t["amount"] for t in txns) / 100, "available": None, "computed": True}


def _txn_payload(t: dict) -> dict:
    src = t.get("source") or {}
    cat = src.get("category")
    detail = src.get(cat) if isinstance(src.get(cat), dict) else {}
    addenda = ((detail.get("addenda") or {}).get("freeform") or {}).get("entries") or []
    return {
        "amount": t["amount"] / 100, "direction": "in" if t["amount"] > 0 else "out",
        "currency": t.get("currency"), "description": t.get("description"), "created_at": t.get("created_at"),
        "category": cat, "originator": detail.get("originator_company_name"),
        "entry_description": detail.get("originator_company_entry_description"),
        "discretionary_data": detail.get("originator_company_discretionary_data"),
        "addenda": [e.get("payment_related_information") for e in addenda],
        "trace_number": detail.get("trace_number"), "transfer_id": detail.get("transfer_id"),
        "increase_transaction_id": t["id"],
    }


def track(conn, b: Increase, ctx: dict) -> dict:
    """Every new bank transaction becomes a bank.transaction event (entity banktxn:<id>)."""
    bank = load_bank()
    new = 0
    for t in b.list_all("/transactions", {"account_id": bank["account_id"]}):
        entity = f"banktxn:{t['id']}"
        if conn.execute("SELECT 1 FROM events WHERE entity = ? LIMIT 1", (entity,)).fetchone():
            continue
        db.append(conn, kind="bank.transaction", entity=entity, payload=_txn_payload(t),
                  sources=[f"increase:transaction/{t['id']}"], **ctx)
        new += 1
    return {"new_transactions": new}


def simulate_inbound(conn, b: Increase, amount: float, company: str, description: str, ctx: dict,
                     addenda: str | None = None, discretionary: str | None = None) -> dict:
    """Sandbox only: a customer's ACH credit arrives."""
    bank = load_bank()
    body: dict[str, Any] = {"account_number_id": bank["account_number_id"], "amount": int(round(amount * 100)),
                            "company_name": company[:16], "company_entry_description": description[:10]}
    if discretionary:
        body["company_discretionary_data"] = discretionary[:20]
    if addenda:
        body["addenda"] = {"category": "freeform", "freeform": {"entries": [{"payment_related_information": addenda[:80]}]}}
    t = b.request("POST", "/simulations/inbound_ach_transfers", body=body)
    db.append(conn, kind="sim.inbound_ach", entity=f"sim:ACH-{t['id']}",
              payload={"amount": amount, "company": company, "description": description, "addenda": addenda,
                       "status": t.get("status")}, sources=[f"increase:inbound_ach_transfer/{t['id']}"], **ctx)
    return {"inbound_ach_transfer": t["id"], "status": t.get("status")}


def vendor_accounts_on_file(vendor: str) -> set[tuple[str, str]]:
    """(routing, account) pairs Odoo has for this vendor: res.partner.bank, routing in the bank's BIC."""
    from . import odoo
    o = odoo.Odoo()
    partners = o.search_read("res.partner", [("name", "=ilike", vendor), ("supplier_rank", ">", 0)], ["id"])
    if not partners:
        return set()
    out = set()
    for acct in o.search_read("res.partner.bank", [("partner_id", "in", [p["id"] for p in partners])],
                              ["acc_number", "bank_id"]):
        if acct["bank_id"]:
            bic = o.read("res.bank", [acct["bank_id"][0]], ["bic"])[0]["bic"]
            out.add((str(bic or ""), str(acct["acc_number"])))
    return out


def send(conn, b: Increase, entity: str, ctx: dict) -> dict:
    """Pay a vendor by ACH, only if the ledger's approval gate passes for this bill payment."""
    db.check_post(conn, entity)
    pay = db.latest_payload(conn, entity) or {}
    dest = pay.get("vendor_bank") or {}
    if not dest.get("routing_number") or not dest.get("account_number"):
        raise BankError(f"{entity} has no vendor_bank routing_number/account_number")
    # Pay only to the vendor's bank account on file in the books, whatever the payload says.
    # The payload was written by an agent; the vendor master is the source of truth.
    on_file = vendor_accounts_on_file(pay.get("vendor") or "")
    if (str(dest["routing_number"]), str(dest["account_number"])) not in on_file:
        raise BankError(f"{entity}: {dest['routing_number']}/{dest['account_number']} is not a bank account on file "
                        f"for {pay.get('vendor')!r} in Odoo ({len(on_file)} on file). Refusing to pay.")
    amount = round(sum(float(a["amount"]) for a in pay.get("applications", [])), 2)
    bank = load_bank()
    t = b.request("POST", "/ach_transfers", idempotency_key=entity, body={
        "account_id": bank["account_id"], "amount": int(round(amount * 100)),
        "routing_number": dest["routing_number"], "account_number": dest["account_number"],
        "statement_descriptor": f"{entity.split(':', 1)[1]} {pay.get('vendor', '')}"[:200],
        "company_name": (pay.get("vendor") or "Vendor")[:16]})
    try:
        b.request("POST", f"/simulations/ach_transfers/{t['id']}/settle")  # sandbox: settle now
    except BankError:
        pass
    return {"sent": db.append(conn, kind="billpayment.sent", entity=entity,
                              payload={"ach_transfer_id": t["id"], "amount": amount, "status": t.get("status")},
                              sources=[f"increase:ach_transfer/{t['id']}"], **ctx),
            "ach_transfer": t["id"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="connectors.increase", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_ctx(sp):
        sp.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"))
        sp.add_argument("--role", default=os.environ.get("LEDGER_ROLE"))
        sp.add_argument("--run-id", default=os.environ.get("LEDGER_RUN_ID"))
        sp.add_argument("--workflow", default=os.environ.get("LEDGER_WORKFLOW"))
        sp.add_argument("--workflow-version", type=int)
        return sp

    sub.add_parser("setup")
    sub.add_parser("balance")
    with_ctx(sub.add_parser("track"))
    si = with_ctx(sub.add_parser("simulate-inbound"))
    si.add_argument("amount", type=float)
    si.add_argument("--company", required=True)
    si.add_argument("--description", required=True)
    si.add_argument("--addenda")
    si.add_argument("--discretionary")
    with_ctx(sub.add_parser("send")).add_argument("entity")

    args = p.parse_args(argv)
    out = lambda o: sys.stdout.write(json.dumps(o, indent=2, default=str) + "\n")
    try:
        b = Increase()
        if args.cmd == "setup":
            out(setup(b))
            return 0
        if args.cmd == "balance":
            out(balance(b))
            return 0
        if not args.actor or not args.role:
            raise db.LedgerError("--actor and --role are required (or LEDGER_ACTOR / LEDGER_ROLE)")
        ctx = {"actor": args.actor, "role": args.role, "run_id": args.run_id, "workflow": args.workflow,
               "workflow_version": args.workflow_version}
        conn = db.connect(Path(args.db) if args.db else None)
        if args.cmd == "track":
            out(track(conn, b, ctx))
        elif args.cmd == "simulate-inbound":
            out(simulate_inbound(conn, b, args.amount, args.company, args.description, ctx,
                                 args.addenda, args.discretionary))
        elif args.cmd == "send":
            out(send(conn, b, args.entity, ctx))
        return 0
    except (BankError, db.LedgerError) as e:
        out({"error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
