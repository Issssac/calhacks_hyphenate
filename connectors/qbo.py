"""QuickBooks Online connector.

Agents never call the QBO API directly. They call this module, which:

* refuses to post anything the ledger's posting gate has not cleared
  (approval after the latest draft, approved parent, or a 3-way match);
* records every post in the ledger as qbo.posted, with the QBO id;
* writes the matching journal entry for invoices, bills and cash, so our
  journal can be reconciled against QBO's trial balance;
* tracks what changes on the QBO side (CDC) into qbo.changed events.

    python -m connectors.qbo connect          one-time OAuth, opens a consent URL
    python -m connectors.qbo whoami
    python -m connectors.qbo survey           chart of accounts, items, parties, prefs
    python -m connectors.qbo mapping --write  pick accounts, save structure/qbo_mapping.json
    python -m connectors.qbo post so:SO-1001 --actor orders-1 --role orders
    python -m connectors.qbo record-payment 145 --actor cash-1 --role cash
    python -m connectors.qbo apply-payment payment:P-145 --apply invoice:INV-1001=500 --actor cash-1 --role cash
    python -m connectors.qbo track --actor tracker-1
    python -m connectors.qbo query "select * from Invoice maxresults 5"
    python -m connectors.qbo report TrialBalance

QBO has no sales-order object, so a sales order is posted as an Estimate with
status Accepted, and its invoice links to that Estimate.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ledgerdb import db
from .env import REPO_ROOT, need, read_token, write_token

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "75"
MAPPING_PATH = REPO_ROOT / "structure" / "qbo_mapping.json"


class QBOError(RuntimeError):
    pass


# ---------------------------------------------------------------- client

class QBO:
    def __init__(self) -> None:
        cfg = need("QBO_CLIENT_ID", "QBO_CLIENT_SECRET")
        self.client_id = cfg["QBO_CLIENT_ID"]
        self.client_secret = cfg["QBO_CLIENT_SECRET"]
        self.env = os.environ.get("QBO_ENV", "sandbox")
        self.base = ("https://sandbox-quickbooks.api.intuit.com" if self.env == "sandbox"
                     else "https://quickbooks.api.intuit.com")
        self.token = read_token("qbo")
        if not self.token.get("refresh_token") and os.environ.get("QBO_REFRESH_TOKEN"):
            self.token["refresh_token"] = os.environ["QBO_REFRESH_TOKEN"]
        self.realm = self.token.get("realm_id") or os.environ.get("QBO_REALM_ID")
        self.session = requests.Session()

    # -- auth

    def _basic(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _store(self, tok: dict[str, Any]) -> None:
        self.token.update(tok)
        self.token["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        if self.realm:
            self.token["realm_id"] = self.realm
        write_token("qbo", self.token)

    def refresh(self) -> None:
        if not self.token.get("refresh_token"):
            raise QBOError("no refresh token: run `python -m connectors.qbo connect` first")
        r = requests.post(TOKEN_URL, headers={"Authorization": self._basic(), "Accept": "application/json"},
                          data={"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]},
                          timeout=30)
        if r.status_code != 200:
            raise QBOError(f"token refresh failed: {r.status_code} {r.text[:300]}")
        self._store(r.json())  # Intuit rotates refresh tokens, so always keep the new one

    def exchange_code(self, code: str, redirect_uri: str, realm: str) -> None:
        r = requests.post(TOKEN_URL, headers={"Authorization": self._basic(), "Accept": "application/json"},
                          data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                          timeout=30)
        if r.status_code != 200:
            raise QBOError(f"code exchange failed: {r.status_code} {r.text[:300]}")
        self.realm = realm
        self._store(r.json())

    def _bearer(self) -> str:
        if not self.token.get("access_token") or self.token.get("expires_at", 0) < time.time():
            self.refresh()
        return "Bearer " + self.token["access_token"]

    # -- http

    def request(self, method: str, path: str, params: dict | None = None, body: Any = None) -> dict:
        if not self.realm:
            raise QBOError("no realm id: run `connect` or set QBO_REALM_ID")
        url = f"{self.base}/v3/company/{self.realm}/{path.lstrip('/')}"
        params = {"minorversion": MINOR_VERSION, **(params or {})}
        for attempt in (1, 2):
            r = self.session.request(method, url, params=params, json=body, timeout=60, headers={
                "Authorization": self._bearer(), "Accept": "application/json",
                "Content-Type": "application/json"})
            if r.status_code == 401 and attempt == 1:
                self.refresh()
                continue
            if r.status_code == 429 and attempt == 1:
                time.sleep(5)
                continue
            break
        try:
            data = r.json()
        except ValueError:
            raise QBOError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        fault = data.get("Fault") or (data if r.status_code >= 400 else None)
        if fault:
            errs = fault.get("Error", []) if isinstance(fault, dict) else []
            msg = "; ".join(f"{e.get('Message')}: {e.get('Detail')}" for e in errs) or json.dumps(fault)[:300]
            raise QBOError(f"{method} {path}: {msg}")
        return data

    def query(self, sql: str) -> list[dict]:
        data = self.request("GET", "query", {"query": sql})
        resp = data.get("QueryResponse", {})
        for k, v in resp.items():
            if isinstance(v, list):
                return v
        return []

    def query_all(self, entity: str, where: str = "") -> list[dict]:
        out, start = [], 1
        while True:
            page = self.query(f"select * from {entity} {where} startposition {start} maxresults 1000")
            out += page
            if len(page) < 1000:
                return out
            start += 1000

    def get(self, entity: str, ident: str) -> dict:
        return self.request("GET", f"{entity.lower()}/{ident}")[entity]

    def create(self, entity: str, body: dict) -> dict:
        return self.request("POST", entity.lower(), body=body)[entity]

    def sparse_update(self, entity: str, obj: dict, fields: dict) -> dict:
        body = {"Id": obj["Id"], "SyncToken": obj["SyncToken"], "sparse": True, **fields}
        return self.request("POST", entity.lower(), body=body)[entity]

    def cdc(self, entities: list[str], since: str) -> dict[str, list[dict]]:
        data = self.request("GET", "cdc", {"entities": ",".join(entities), "changedSince": since})
        out: dict[str, list[dict]] = {}
        for block in data.get("CDCResponse", []):
            for qr in block.get("QueryResponse", []):
                for k, v in qr.items():
                    if isinstance(v, list):
                        out.setdefault(k, []).extend(v)
        return out

    def report(self, name: str, params: dict | None = None) -> dict:
        return self.request("GET", f"reports/{name}", params or {})

    # -- master data

    @staticmethod
    def _q(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'")

    def find(self, entity: str, name: str, field: str = "DisplayName") -> dict | None:
        rows = self.query(f"select * from {entity} where {field} = '{self._q(name)}'")
        return rows[0] if rows else None

    def ensure_customer(self, name: str, email: str | None = None) -> dict:
        found = self.find("Customer", name)
        if found:
            return found
        body: dict[str, Any] = {"DisplayName": name[:100]}
        if email:
            body["PrimaryEmailAddr"] = {"Address": email}
        return self.create("Customer", body)

    def ensure_vendor(self, name: str, email: str | None = None) -> dict:
        found = self.find("Vendor", name)
        if found:
            return found
        body: dict[str, Any] = {"DisplayName": name[:100]}
        if email:
            body["PrimaryEmailAddr"] = {"Address": email}
        return self.create("Vendor", body)

    def ensure_item(self, name: str, mapping: dict, service: bool = False) -> dict:
        found = self.find("Item", name, field="Name")
        if found:
            return found
        return self.create("Item", {
            "Name": name[:100],
            "Type": "Service" if service else "NonInventory",
            "IncomeAccountRef": {"value": mapping["income_account"]["value"]},
            "ExpenseAccountRef": {"value": mapping["cogs_account"]["value"]},
        })


# ---------------------------------------------------------------- mapping

def discover_mapping(q: QBO) -> dict:
    """Pick the accounts we post to. The architect reviews and may override this."""
    accounts = [a for a in q.query_all("Account") if a.get("Active", True)]

    def pick(acct_type: str, prefer: list[str]) -> dict | None:
        cands = [a for a in accounts if a.get("AccountType") == acct_type]
        for p in prefer:
            for a in cands:
                if p.lower() in a["Name"].lower():
                    return {"value": a["Id"], "name": a["FullyQualifiedName"]}
        return {"value": cands[0]["Id"], "name": cands[0]["FullyQualifiedName"]} if cands else None

    return {
        "income_account": pick("Income", ["Sales of Product", "Sales", "Services"]),
        "cogs_account": pick("Cost of Goods Sold", ["Cost of Goods", "Supplies"]),
        "ar_account": pick("Accounts Receivable", ["Receivable"]),
        "ap_account": pick("Accounts Payable", ["Payable"]),
        "bank_account": pick("Bank", ["Checking", "Operating"]),
        "undeposited_account": pick("Other Current Asset", ["Undeposited"]),
        "sales_tax_account": pick("Other Current Liability", ["Sales Tax", "Board of Equalization"]),
    }


def load_mapping(q: QBO | None = None) -> dict:
    if MAPPING_PATH.exists():
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    if q is None:
        raise QBOError("no structure/qbo_mapping.json yet: run `mapping --write`")
    m = discover_mapping(q)
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_PATH.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return m


def survey(q: QBO) -> dict:
    accounts = q.query_all("Account")
    return {
        "company": q.request("GET", f"companyinfo/{q.realm}").get("CompanyInfo", {}),
        "preferences": q.request("GET", "preferences").get("Preferences", {}),
        "accounts": [{"id": a["Id"], "name": a["FullyQualifiedName"], "type": a.get("AccountType"),
                      "subtype": a.get("AccountSubType"), "balance": a.get("CurrentBalance")}
                     for a in accounts],
        "items": [{"id": i["Id"], "name": i["Name"], "type": i.get("Type"),
                   "price": i.get("UnitPrice"), "cost": i.get("PurchaseCost")}
                  for i in q.query_all("Item")],
        "customers": [{"id": c["Id"], "name": c["DisplayName"],
                       "email": (c.get("PrimaryEmailAddr") or {}).get("Address"),
                       "balance": c.get("Balance")} for c in q.query_all("Customer")],
        "vendors": [{"id": v["Id"], "name": v["DisplayName"],
                     "email": (v.get("PrimaryEmailAddr") or {}).get("Address")}
                    for v in q.query_all("Vendor")],
        "terms": [{"id": t["Id"], "name": t["Name"]} for t in q.query_all("Term")],
        "tax_codes": [{"id": t["Id"], "name": t["Name"]} for t in q.query_all("TaxCode")],
        "mapping_guess": discover_mapping(q),
    }


# ---------------------------------------------------------------- builders

def _money(x: Any) -> float:
    return round(float(x), 2)


def _sales_lines(q: QBO, mapping: dict, lines: list[dict]) -> list[dict]:
    out = []
    for ln in lines:
        item = q.ensure_item(ln["item"], mapping, service=ln.get("service", False))
        qty = float(ln.get("qty", 1))
        price = float(ln["unit_price"]) if "unit_price" in ln else float(ln["amount"]) / qty
        out.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": _money(ln.get("amount", qty * price)),
            "Description": ln.get("description", ln["item"])[:4000],
            "SalesItemLineDetail": {
                "ItemRef": {"value": item["Id"]}, "Qty": qty, "UnitPrice": price,
                **({"TaxCodeRef": {"value": ln["tax_code"]}} if ln.get("tax_code") else {}),
            },
        })
    return out


def _expense_lines(q: QBO, mapping: dict, lines: list[dict], po_link: dict | None = None) -> list[dict]:
    out = []
    for i, ln in enumerate(lines):
        item = q.ensure_item(ln["item"], mapping, service=ln.get("service", False))
        qty = float(ln.get("qty", 1))
        cost = float(ln["unit_cost"]) if "unit_cost" in ln else float(ln["amount"]) / qty
        line = {
            "DetailType": "ItemBasedExpenseLineDetail",
            "Amount": _money(ln.get("amount", qty * cost)),
            "Description": ln.get("description", ln["item"])[:4000],
            "ItemBasedExpenseLineDetail": {"ItemRef": {"value": item["Id"]}, "Qty": qty, "UnitPrice": cost},
        }
        if po_link and i < len(po_link.get("line_ids", [])):
            line["LinkedTxn"] = [{"TxnId": po_link["qbo_id"], "TxnType": "PurchaseOrder",
                                  "TxnLineId": po_link["line_ids"][i]}]
        out.append(line)
    return out


def _doc(entity: str) -> str:
    return entity.split(":", 1)[1][:21]


def _parent_payload(conn, entity: str, rel: str) -> tuple[str | None, dict | None]:
    parents = db.links_of(conn, entity, rel)
    if not parents:
        return None, None
    return parents[0], db.latest_payload(conn, parents[0])


def build(conn, q: QBO, mapping: dict, entity: str, payload: dict) -> tuple[str, dict, dict]:
    """Ledger document -> (QBO type, request body, notes)."""
    etype = entity.split(":", 1)[0]
    note = f"ledger {entity}"
    notes: dict[str, Any] = {}

    if etype == "so":
        cust = q.ensure_customer(payload["customer"], payload.get("customer_email"))
        return "Estimate", {
            "CustomerRef": {"value": cust["Id"]}, "DocNumber": _doc(entity), "TxnStatus": "Accepted",
            "Line": _sales_lines(q, mapping, payload["lines"]), "PrivateNote": note,
            **({"TxnDate": payload["date"]} if payload.get("date") else {}),
        }, notes

    if etype == "po":
        vend = q.ensure_vendor(payload["vendor"], payload.get("vendor_email"))
        body = {"VendorRef": {"value": vend["Id"]}, "DocNumber": _doc(entity),
                "Line": _expense_lines(q, mapping, payload["lines"]), "Memo": note}
        if mapping.get("ap_account"):
            body["APAccountRef"] = {"value": mapping["ap_account"]["value"]}
        return "PurchaseOrder", body, notes

    if etype == "invoice":
        so, so_payload = _parent_payload(conn, entity, "from_so")
        src = payload if payload.get("lines") else (so_payload or {})
        cust = q.ensure_customer(src["customer"], src.get("customer_email"))
        body = {"CustomerRef": {"value": cust["Id"]}, "DocNumber": _doc(entity),
                "Line": _sales_lines(q, mapping, src["lines"]), "PrivateNote": note}
        est = db.qbo_ref(conn, so) if so else None
        if est:
            body["LinkedTxn"] = [{"TxnId": est["qbo_id"], "TxnType": "Estimate"}]
        for k_src, k_dst in (("due_date", "DueDate"), ("date", "TxnDate")):
            if payload.get(k_src):
                body[k_dst] = payload[k_src]
        return "Invoice", body, notes

    if etype == "bill":
        po, _ = _parent_payload(conn, entity, "from_po")
        vend = q.ensure_vendor(payload["vendor"], payload.get("vendor_email"))
        po_link = db.qbo_ref(conn, po) if po else None
        body = {"VendorRef": {"value": vend["Id"]}, "Line": _expense_lines(q, mapping, payload["lines"], po_link),
                "PrivateNote": note}
        if payload.get("vendor_ref"):
            body["DocNumber"] = str(payload["vendor_ref"])[:21]
        for k_src, k_dst in (("due_date", "DueDate"), ("date", "TxnDate")):
            if payload.get(k_src):
                body[k_dst] = payload[k_src]
        notes["po_linked"] = bool(po_link)
        return "Bill", body, notes

    if etype == "billpayment":
        vend = q.ensure_vendor(payload["vendor"])
        lines = []
        for app in payload["applications"]:
            ref = db.qbo_ref(conn, app["bill"])
            if not ref:
                raise QBOError(f"{app['bill']} was never posted")
            lines.append({"Amount": _money(app["amount"]),
                          "LinkedTxn": [{"TxnId": ref["qbo_id"], "TxnType": "Bill"}]})
        return "BillPayment", {
            "VendorRef": {"value": vend["Id"]}, "PayType": "Check",
            "CheckPayment": {"BankAccountRef": {"value": mapping["bank_account"]["value"]}},
            "TotalAmt": _money(sum(a["amount"] for a in payload["applications"])),
            "Line": lines, "PrivateNote": note,
        }, notes

    raise QBOError(f"don't know how to post {etype!r}")


def _journal_for(etype: str, obj: dict, mapping: dict) -> list[dict] | None:
    """The journal entry QBO books for a document, mirrored into our ledger."""
    name = lambda k: (mapping.get(k) or {}).get("name", k)
    total = _money(obj.get("TotalAmt", 0))
    if total <= 0:
        return None
    tax = _money((obj.get("TxnTaxDetail") or {}).get("TotalTax", 0))
    if etype == "invoice":
        lines = [{"account": name("ar_account"), "debit": total},
                 {"account": name("income_account"), "credit": _money(total - tax)}]
        if tax:
            lines.append({"account": name("sales_tax_account"), "credit": tax})
        return lines
    if etype == "bill":
        return [{"account": name("cogs_account"), "debit": total},
                {"account": name("ap_account"), "credit": total}]
    if etype == "payment":
        return [{"account": name("undeposited_account"), "debit": total},
                {"account": name("ar_account"), "credit": total}]
    if etype == "billpayment":
        return [{"account": name("ap_account"), "debit": total},
                {"account": name("bank_account"), "credit": total}]
    return None


def _ctx(args) -> dict:
    return {"actor": args.actor, "role": args.role, "run_id": args.run_id,
            "workflow": args.workflow, "workflow_version": args.workflow_version}


def post(conn, q: QBO, entity: str, ctx: dict) -> dict:
    payload = db.latest_payload(conn, entity)
    if payload is None:
        raise QBOError(f"{entity} has no draft in the ledger")
    db.check_post(conn, entity)  # the gate: refuses before anything reaches QBO
    mapping = load_mapping(q)
    qbo_type, body, notes = build(conn, q, mapping, entity, payload)
    try:
        obj = q.create(qbo_type, body)
    except QBOError as e:
        if qbo_type == "Bill" and "LinkedTxn" in json.dumps(body):
            # Some companies refuse line-level PO links; post unlinked and say so.
            for ln in body["Line"]:
                ln.pop("LinkedTxn", None)
            obj = q.create(qbo_type, body)
            notes["po_linked"] = False
            notes["po_link_error"] = str(e)
        else:
            db.append(conn, kind="qbo.failed", entity=entity, payload={"qbo_type": qbo_type, "error": str(e)},
                      **ctx)
            raise
    rec = {
        "qbo_type": qbo_type, "qbo_id": obj["Id"], "sync_token": obj.get("SyncToken"),
        "doc_number": obj.get("DocNumber"), "total": obj.get("TotalAmt"),
        "tax": (obj.get("TxnTaxDetail") or {}).get("TotalTax"),
        "line_ids": [ln["Id"] for ln in obj.get("Line", []) if ln.get("Id")],
        **notes,
    }
    out = {"posted": db.append(conn, kind="qbo.posted", entity=entity, payload=rec,
                               sources=[f"qbo:{qbo_type}/{obj['Id']}"], **ctx), "qbo": rec}
    etype = entity.split(":", 1)[0]
    lines = _journal_for(etype, obj, mapping)
    if lines and db.load_policy().allowed(ctx["role"], "je.posted"):
        out["journal"] = db.append(
            conn, kind="je.posted", entity=f"je:{entity.replace(':', '-')}",
            payload={"memo": f"{qbo_type} {obj.get('DocNumber') or obj['Id']} for {entity}", "lines": lines},
            links=[{"rel": "books", "to": entity}], sources=[f"qbo:{qbo_type}/{obj['Id']}"], **ctx)
    return out


def record_payment(conn, q: QBO, qbo_id: str, ctx: dict) -> dict:
    """A customer payment that landed in QBO (from CDC) becomes payment.received + its journal entry."""
    entity = f"payment:P-{qbo_id}"
    if db.entity_for_qbo(conn, "Payment", qbo_id) or conn.execute(
            "SELECT 1 FROM events WHERE entity = ? AND kind = 'payment.received'", (entity,)).fetchone():
        return {"already": entity}
    obj = q.get("Payment", qbo_id)
    mapping = load_mapping(q)
    cust = obj.get("CustomerRef", {})
    applied = [{"qbo_invoice": lt["TxnId"], "amount": ln.get("Amount")}
               for ln in obj.get("Line", []) for lt in ln.get("LinkedTxn", []) if lt.get("TxnType") == "Invoice"]
    payload = {"customer": cust.get("name"), "customer_qbo_id": cust.get("value"), "total": obj.get("TotalAmt"),
               "unapplied": obj.get("UnappliedAmt"), "date": obj.get("TxnDate"),
               "reference": obj.get("PaymentRefNum"), "memo": obj.get("PrivateNote"),
               "already_applied_in_qbo": applied, "qbo_id": qbo_id, "sync_token": obj.get("SyncToken")}
    out = {"received": db.append(conn, kind="payment.received", entity=entity, payload=payload,
                                 sources=[f"qbo:Payment/{qbo_id}"], **ctx)}
    lines = _journal_for("payment", obj, mapping)
    if lines:
        out["journal"] = db.append(conn, kind="je.posted", entity=f"je:{entity.replace(':', '-')}",
                                   payload={"memo": f"Customer payment {qbo_id}", "lines": lines},
                                   links=[{"rel": "books", "to": entity}], sources=[f"qbo:Payment/{qbo_id}"],
                                   **ctx)
    return out


def apply_payment(conn, q: QBO, entity: str, applications: list[tuple[str, float]], ctx: dict,
                  reason: str = "") -> dict:
    """Apply a received payment to invoices in QBO, then record payment.applied."""
    pay = db.latest_payload(conn, entity)
    if not pay or not pay.get("qbo_id"):
        raise QBOError(f"{entity} is not a recorded QBO payment")
    total = sum(a for _, a in applications)
    if round(total, 2) > round(float(pay["total"]), 2):
        raise QBOError(f"applying {total} exceeds the payment's {pay['total']}")
    lines = []
    for inv, amount in applications:
        ref = db.qbo_ref(conn, inv)
        if not ref:
            raise QBOError(f"{inv} was never posted to QBO")
        lines.append({"Amount": _money(amount), "LinkedTxn": [{"TxnId": ref["qbo_id"], "TxnType": "Invoice"}]})
    obj = q.get("Payment", pay["qbo_id"])
    updated = q.sparse_update("Payment", obj, {"TotalAmt": obj["TotalAmt"], "Line": lines})
    return db.append(conn, kind="payment.applied", entity=entity,
                     payload={"applications": [{"invoice": i, "amount": a} for i, a in applications],
                              "unapplied": updated.get("UnappliedAmt"), "reason": reason},
                     links=[{"rel": "applies_to", "to": i, "amount": a} for i, a in applications],
                     sources=[f"qbo:Payment/{pay['qbo_id']}"], **ctx)


TRACKED = ["Estimate", "PurchaseOrder", "Invoice", "Bill", "Payment", "BillPayment"]


def track(conn, q: QBO, ctx: dict, since: str | None = None) -> dict:
    """Pull QBO changes since the last cursor into qbo.changed events."""
    row = conn.execute(
        "SELECT json_extract(payload, '$.cursor') FROM events WHERE entity = 'tracker:qbo'"
        " ORDER BY seq DESC LIMIT 1").fetchone()
    started = datetime.now(timezone.utc)
    since = since or (row[0] if row and row[0] else (started - timedelta(days=29)).isoformat(timespec="seconds"))
    changes = q.cdc(TRACKED, since)
    n = 0
    for qbo_type, objs in changes.items():
        for obj in objs:
            entity = db.entity_for_qbo(conn, qbo_type, obj["Id"]) or f"qbo:{qbo_type}-{obj['Id']}"
            db.append(conn, kind="qbo.changed", entity=entity, sources=[f"qbo:{qbo_type}/{obj['Id']}"], payload={
                "qbo_type": qbo_type, "qbo_id": obj["Id"], "status": obj.get("status"),
                "sync_token": obj.get("SyncToken"), "total": obj.get("TotalAmt"),
                "balance": obj.get("Balance"), "txn_status": obj.get("TxnStatus"),
                "unapplied": obj.get("UnappliedAmt"), "updated": (obj.get("MetaData") or {}).get("LastUpdatedTime"),
                "memo": obj.get("PrivateNote"),
            }, **ctx)
            n += 1
    db.append(conn, kind="qbo.changed", entity="tracker:qbo",
              payload={"cursor": started.isoformat(timespec="seconds"), "since": since, "changes": n}, **ctx)
    return {"since": since, "changes": n}


# ---------------------------------------------------------------- oauth

def connect(port: int = 8765) -> dict:
    q = QBO()
    redirect = os.environ.get("QBO_REDIRECT_URI", f"http://localhost:{port}/callback")
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": q.client_id, "response_type": "code", "scope": SCOPE,
        "redirect_uri": redirect, "state": state})
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            got.update(qs)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"QuickBooks connected. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", port), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print(json.dumps({"open_this_url": url}), flush=True)
    deadline = time.time() + 600
    while not got and time.time() < deadline:
        time.sleep(0.5)
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise QBOError(f"OAuth did not complete: {got}")
    q.exchange_code(got["code"], redirect, got["realmId"])
    return {"ok": True, "realm_id": got["realmId"]}


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="connectors.qbo", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_ctx(sp):
        sp.add_argument("--actor", default=os.environ.get("LEDGER_ACTOR"), required=not os.environ.get("LEDGER_ACTOR"))
        sp.add_argument("--role", default=os.environ.get("LEDGER_ROLE"), required=not os.environ.get("LEDGER_ROLE"))
        sp.add_argument("--run-id", default=os.environ.get("LEDGER_RUN_ID"))
        sp.add_argument("--workflow", default=os.environ.get("LEDGER_WORKFLOW"))
        sp.add_argument("--workflow-version", type=int, default=None)
        return sp

    sub.add_parser("connect").add_argument("--port", type=int, default=8765)
    sub.add_parser("whoami")
    sub.add_parser("survey")
    sub.add_parser("mapping").add_argument("--write", action="store_true")
    sq = sub.add_parser("query")
    sq.add_argument("sql")
    sr = sub.add_parser("report")
    sr.add_argument("name")
    sr.add_argument("params", nargs="*", help="key=value")
    sg = sub.add_parser("get")
    sg.add_argument("entity_type")
    sg.add_argument("id")
    with_ctx(sub.add_parser("post")).add_argument("entity")
    with_ctx(sub.add_parser("record-payment")).add_argument("qbo_id")
    ap = with_ctx(sub.add_parser("apply-payment"))
    ap.add_argument("entity")
    ap.add_argument("--apply", action="append", required=True, help="invoice:INV-1=100.00")
    ap.add_argument("--reason", default="")
    tr = with_ctx(sub.add_parser("track"))
    tr.add_argument("--since")

    args = p.parse_args(argv)
    out = lambda o: sys.stdout.write(json.dumps(o, indent=2, default=str) + "\n")
    try:
        if args.cmd == "connect":
            out(connect(args.port))
            return 0
        q = QBO()
        if args.cmd == "whoami":
            info = q.request("GET", f"companyinfo/{q.realm}")["CompanyInfo"]
            out({"realm": q.realm, "env": q.env, "company": info.get("CompanyName"), "country": info.get("Country")})
        elif args.cmd == "survey":
            out(survey(q))
        elif args.cmd == "mapping":
            m = discover_mapping(q)
            if args.write:
                MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
                MAPPING_PATH.write_text(json.dumps(m, indent=2), encoding="utf-8")
            out(m)
        elif args.cmd == "query":
            out(q.query(args.sql))
        elif args.cmd == "report":
            out(q.report(args.name, dict(kv.split("=", 1) for kv in args.params)))
        elif args.cmd == "get":
            out(q.get(args.entity_type, args.id))
        else:
            conn = db.connect(Path(args.db) if args.db else None)
            ctx = _ctx(args)
            if args.cmd == "post":
                out(post(conn, q, args.entity, ctx))
            elif args.cmd == "record-payment":
                out(record_payment(conn, q, args.qbo_id, ctx))
            elif args.cmd == "apply-payment":
                apps = []
                for a in args.apply:
                    inv, _, amt = a.rpartition("=")
                    apps.append((inv, float(amt)))
                out(apply_payment(conn, q, args.entity, apps, ctx, args.reason))
            elif args.cmd == "track":
                out(track(conn, q, ctx, args.since))
        return 0
    except (QBOError, db.LedgerError) as e:
        out({"error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
