"""Odoo connector (Odoo 18, JSON-RPC).

Agents never call Odoo directly. They call this module, which keeps Odoo's
own lifecycle and puts the ledger's gate in the middle of it:

    stage  creates or updates the draft in Odoo (quotation, RFQ, draft invoice,
           draft bill). No gate: a draft changes nothing in the books.
    post   confirms it (action_confirm / button_confirm / action_post), but only
           after `ledgerdb.check_post` passes: approval after the latest draft,
           an approved parent, or a 3-way match.

After every post it reads the journal lines Odoo actually booked and mirrors
them into the ledger as je.posted, so reconcile compares like with like.

    python -m connectors.odoo whoami
    python -m connectors.odoo survey
    python -m connectors.odoo stage so:SO-1001 --actor orders-1 --role orders
    python -m connectors.odoo post so:SO-1001 --actor orders-1 --role orders
    python -m connectors.odoo three-way bill:B-1001
    python -m connectors.odoo register-payment payment:P-1 --apply invoice:INV-1001=450 --actor cash-1 --role cash
    python -m connectors.odoo track --actor tracker-1 --role tracker
    python -m connectors.odoo tb
    python -m connectors.odoo read sale.order '[["name","=","S00001"]]' name state amount_total

Draft payloads (what agents write to the ledger before staging):

    so / invoice   {"customer", "customer_email"?, "lines": [{"item", "qty", "unit_price",
                    "description"?, "taxes"?: [names]}], "total" (untaxed), "date"?}
    po / bill      {"vendor", "vendor_email"?, "vendor_ref"?, "lines": [{"item", "qty", "unit_cost",
                    "description"?}], "total" (untaxed), "date"?, "due_date"?}
    billpayment    {"vendor", "applications": [{"bill": "bill:B-1", "amount"}], "bank_transfer"?}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ledgerdb import db
from .env import load_env


class OdooError(RuntimeError):
    pass


# ---------------------------------------------------------------- client

class Odoo:
    def __init__(self) -> None:
        load_env()
        self.url = os.environ.get("ODOO_URL", "http://localhost:8069").rstrip("/")
        self.db = os.environ.get("ODOO_DB", "revenue")
        self.user = os.environ.get("ODOO_USER", "admin")
        self.password = os.environ.get("ODOO_PASSWORD", "admin")
        self.session = requests.Session()
        self._uid: int | None = None

    def _rpc(self, service: str, method: str, *args: Any) -> Any:
        r = self.session.post(f"{self.url}/jsonrpc", timeout=120, json={
            "jsonrpc": "2.0", "method": "call",
            "params": {"service": service, "method": method, "args": list(args)}})
        data = r.json()
        if "error" in data:
            err = data["error"].get("data", {})
            raise OdooError(err.get("message") or json.dumps(data["error"])[:500])
        return data["result"]

    @property
    def uid(self) -> int:
        if self._uid is None:
            self._uid = self._rpc("common", "login", self.db, self.user, self.password)
            if not self._uid:
                raise OdooError(f"login failed for {self.user} on {self.db}")
        return self._uid

    def call(self, model: str, method: str, *args: Any, context: dict | None = None, **kw: Any) -> Any:
        if context:
            kw["context"] = context
        return self._rpc("object", "execute_kw", self.db, self.uid, self.password, model, method, list(args), kw)

    def search_read(self, model: str, domain: list, fields: list[str], **kw: Any) -> list[dict]:
        return self.call(model, "search_read", domain, fields=fields, **kw)

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        return self.call(model, "read", ids, fields)

    def create(self, model: str, vals: dict, context: dict | None = None) -> int:
        return self.call(model, "create", vals, context=context)

    def write(self, model: str, ids: list[int], vals: dict) -> bool:
        return self.call(model, "write", ids, vals)

    # -- master data

    def ensure_partner(self, name: str, email: str | None = None, vendor: bool = False) -> int:
        found = self.search_read("res.partner", [("name", "=ilike", name), ("parent_id", "=", False)], ["id"],
                                 limit=1)
        rank = "supplier_rank" if vendor else "customer_rank"
        if found:
            self.write("res.partner", [found[0]["id"]], {rank: 1})
            return found[0]["id"]
        vals: dict[str, Any] = {"name": name, "is_company": True, rank: 1}
        if email:
            vals["email"] = email
        return self.create("res.partner", vals)

    def ensure_product(self, name: str, price: float | None = None, cost: float | None = None,
                       service: bool = False) -> int:
        found = self.search_read("product.product", ["|", ("name", "=ilike", name), ("default_code", "=", name)],
                                 ["id"], limit=1)
        if found:
            return found[0]["id"]
        vals: dict[str, Any] = {
            "name": name, "type": "service" if service else "consu", "sale_ok": True, "purchase_ok": True,
            "invoice_policy": "order", "purchase_method": "purchase" if service else "receive",
        }
        if price is not None:
            vals["list_price"] = price
        if cost is not None:
            vals["standard_price"] = cost
        return self.create("product.product", vals)

    def tax_ids(self, names: list[str], use: str) -> list[int]:
        out = []
        for n in names:
            t = self.search_read("account.tax", [("name", "=", n), ("type_tax_use", "=", use)], ["id"], limit=1)
            if not t:
                raise OdooError(f"no {use} tax named {n!r}")
            out.append(t[0]["id"])
        return out


# ---------------------------------------------------------------- survey

def survey(o: Odoo) -> dict:
    """What the architect reads to learn how these books are set up."""
    company = o.search_read("res.company", [], ["name", "country_id", "currency_id", "chart_template"])
    mods = o.search_read("ir.module.module", [("state", "=", "installed"),
                                              ("name", "in", ["sale_management", "purchase", "stock", "account",
                                                              "sale_stock", "purchase_stock", "account_accountant"])],
                         ["name"])
    return {
        "company": company,
        "installed": sorted(m["name"] for m in mods),
        "accounts": o.search_read("account.account", [], ["code", "name", "account_type", "reconcile"]),
        "journals": o.search_read("account.journal", [], ["name", "code", "type", "default_account_id"]),
        "taxes": o.search_read("account.tax", [("active", "=", True)],
                               ["name", "type_tax_use", "amount", "amount_type", "price_include"]),
        "payment_terms": o.search_read("account.payment.term", [], ["name"]),
        "products": o.search_read("product.product", [("active", "=", True)],
                                  ["name", "default_code", "type", "list_price", "standard_price",
                                   "invoice_policy", "purchase_method", "seller_ids", "taxes_id",
                                   "supplier_taxes_id"]),
        "vendor_prices": o.search_read("product.supplierinfo", [],
                                       ["partner_id", "product_tmpl_id", "price", "min_qty", "delay"]),
        "customers": o.search_read("res.partner", [("customer_rank", ">", 0)],
                                   ["name", "email", "property_payment_term_id", "credit", "vat"]),
        "vendors": o.search_read("res.partner", [("supplier_rank", ">", 0)],
                                 ["name", "email", "property_supplier_payment_term_id", "debit"]),
        "open_sales": o.search_read("sale.order", [("state", "in", ["draft", "sent", "sale"])],
                                    ["name", "partner_id", "state", "amount_total", "invoice_status"], limit=50),
        "open_purchases": o.search_read("purchase.order", [("state", "in", ["draft", "sent", "purchase"])],
                                        ["name", "partner_id", "state", "amount_total", "invoice_status"],
                                        limit=50),
    }


def trial_balance(o: Odoo) -> list[dict]:
    groups = o.call("account.move.line", "read_group", [("parent_state", "=", "posted")],
                    ["debit:sum", "credit:sum", "balance:sum"], ["account_id"], lazy=False)
    return [{"account": g["account_id"][1], "debit": round(g["debit"], 2), "credit": round(g["credit"], 2),
             "balance": round(g["balance"], 2)} for g in groups]


# ---------------------------------------------------------------- staging

def _sale_lines(o: Odoo, lines: list[dict]) -> list:
    cmds: list = [(5, 0, 0)]
    for ln in lines:
        pid = o.ensure_product(ln["item"], price=ln.get("unit_price"), service=ln.get("service", False))
        vals: dict[str, Any] = {"product_id": pid, "product_uom_qty": float(ln.get("qty", 1)),
                                "price_unit": float(ln["unit_price"])}
        if ln.get("description"):
            vals["name"] = ln["description"]
        if "taxes" in ln:
            vals["tax_id"] = [(6, 0, o.tax_ids(ln["taxes"], "sale"))]
        cmds.append((0, 0, vals))
    return cmds


def _purchase_lines(o: Odoo, lines: list[dict]) -> list:
    cmds: list = [(5, 0, 0)]
    for ln in lines:
        pid = o.ensure_product(ln["item"], cost=ln.get("unit_cost"), service=ln.get("service", False))
        vals: dict[str, Any] = {"product_id": pid, "product_qty": float(ln.get("qty", 1)),
                                "price_unit": float(ln["unit_cost"])}
        if ln.get("description"):
            vals["name"] = ln["description"]
        if "taxes" in ln:
            vals["taxes_id"] = [(6, 0, o.tax_ids(ln["taxes"], "purchase"))]
        cmds.append((0, 0, vals))
    return cmds


def _summary(o: Odoo, model: str, rid: int) -> dict:
    fields = {"sale.order": ["name", "state", "amount_untaxed", "amount_tax", "amount_total", "invoice_status"],
              "purchase.order": ["name", "state", "amount_untaxed", "amount_tax", "amount_total",
                                 "invoice_status", "receipt_status"],
              "account.move": ["name", "state", "move_type", "amount_untaxed", "amount_tax", "amount_total",
                               "amount_residual", "payment_state"],
              "account.payment": ["name", "state", "amount", "partner_id", "move_id"]}[model]
    rec = o.read(model, [rid], fields)[0]
    return {"model": model, "id": rid, **{k: v for k, v in rec.items() if k != "id"}}


def _posted_parent(conn, o: Odoo, entity: str, rel: str) -> tuple[str, dict]:
    parents = db.links_of(conn, entity, rel)
    if not parents:
        raise OdooError(f"{entity} has no {rel} link")
    ref = db.erp_ref(conn, parents[0], posted_only=True)
    if not ref:
        raise OdooError(f"{parents[0]} is not confirmed in Odoo yet")
    return parents[0], ref


def stage(conn, o: Odoo, entity: str, ctx: dict) -> dict:
    """Create or refresh the Odoo draft for a ledger document."""
    payload = db.latest_payload(conn, entity)
    if payload is None:
        raise OdooError(f"{entity} has no draft in the ledger")
    etype = entity.split(":", 1)[0]
    existing = db.erp_ref(conn, entity)
    note = f"ledger {entity}"

    if etype == "so":
        vals = {"partner_id": o.ensure_partner(payload["customer"], payload.get("customer_email")),
                "client_order_ref": payload.get("customer_ref") or entity.split(":", 1)[1],
                "origin": entity, "note": note, "order_line": _sale_lines(o, payload["lines"])}
        model = "sale.order"
    elif etype == "po":
        vals = {"partner_id": o.ensure_partner(payload["vendor"], payload.get("vendor_email"), vendor=True),
                "partner_ref": payload.get("vendor_ref") or "", "origin": entity, "notes": note,
                "order_line": _purchase_lines(o, payload["lines"])}
        model = "purchase.order"
    elif etype == "invoice":
        model = "account.move"
        if not existing:
            _, so = _posted_parent(conn, o, entity, "from_so")
            ctx_wiz = {"active_model": "sale.order", "active_ids": [so["id"]], "active_id": so["id"]}
            wiz = o.create("sale.advance.payment.inv", {"advance_payment_method": "delivered"}, context=ctx_wiz)
            o.call("sale.advance.payment.inv", "create_invoices", [wiz], context=ctx_wiz)
            inv_ids = o.read("sale.order", [so["id"]], ["invoice_ids"])[0]["invoice_ids"]
            drafts = o.search_read("account.move", [("id", "in", inv_ids), ("state", "=", "draft")], ["id"],
                                   order="id desc", limit=1)
            if not drafts:
                raise OdooError("Odoo created no draft invoice (nothing left to invoice on the SO?)")
            rid = drafts[0]["id"]
        else:
            rid = existing["id"]
        upd: dict[str, Any] = {"narration": note}
        if payload.get("date"):
            upd["invoice_date"] = payload["date"]
        if payload.get("due_date"):
            upd["invoice_date_due"] = payload["due_date"]
        o.write(model, [rid], upd)
        rec = _summary(o, model, rid)
        return {"staged": db.append(conn, kind="erp.staged", entity=entity, payload=rec,
                                    sources=[f"odoo:{model}/{rid}"], **ctx), "odoo": rec}
    elif etype == "bill":
        model = "account.move"
        if not existing:
            _, po = _posted_parent(conn, o, entity, "from_po")
            o.call("purchase.order", "action_create_invoice", [po["id"]])
            bill_ids = o.read("purchase.order", [po["id"]], ["invoice_ids"])[0]["invoice_ids"]
            drafts = o.search_read("account.move", [("id", "in", bill_ids), ("state", "=", "draft")], ["id"],
                                   order="id desc", limit=1)
            if not drafts:
                raise OdooError("Odoo created no draft bill (has the receipt been validated?)")
            rid = drafts[0]["id"]
        else:
            rid = existing["id"]
        # Set the draft bill to what the vendor actually billed, line by line.
        lines = o.search_read("account.move.line", [("move_id", "=", rid), ("display_type", "=", "product")],
                              ["id", "product_id", "quantity", "price_unit"])
        by_product = {ln["product_id"][1].lower(): ln for ln in lines if ln["product_id"]}
        cmds = []
        for bl in payload["lines"]:
            match = next((v for k, v in by_product.items() if bl["item"].lower() in k), None)
            if match:
                cmds.append((1, match["id"], {"quantity": float(bl.get("qty", match["quantity"])),
                                              "price_unit": float(bl["unit_cost"])}))
            else:
                pid = o.ensure_product(bl["item"], cost=bl.get("unit_cost"))
                cmds.append((0, 0, {"product_id": pid, "quantity": float(bl.get("qty", 1)),
                                    "price_unit": float(bl["unit_cost"]), "name": bl.get("description", bl["item"])}))
        upd = {"invoice_line_ids": cmds, "narration": note}
        if payload.get("vendor_ref"):
            upd["ref"] = str(payload["vendor_ref"])
        upd["invoice_date"] = payload.get("date") or datetime.now(timezone.utc).date().isoformat()
        if payload.get("due_date"):
            upd["invoice_date_due"] = payload["due_date"]
        o.write(model, [rid], upd)
        rec = _summary(o, model, rid)
        return {"staged": db.append(conn, kind="erp.staged", entity=entity, payload=rec,
                                    sources=[f"odoo:{model}/{rid}"], **ctx), "odoo": rec,
                "three_way": three_way(conn, o, entity)}
    else:
        raise OdooError(f"don't know how to stage {etype!r}")

    if existing:
        state = o.read(model, [existing["id"]], ["state"])[0]["state"]
        if state not in ("draft", "sent"):
            raise OdooError(f"{entity} is already {state} in Odoo; amend it with a new document")
        o.write(model, [existing["id"]], vals)
        rid = existing["id"]
    else:
        rid = o.create(model, vals)
    rec = _summary(o, model, rid)
    return {"staged": db.append(conn, kind="erp.staged", entity=entity, payload=rec,
                                sources=[f"odoo:{model}/{rid}"], **ctx), "odoo": rec}


# ---------------------------------------------------------------- posting

def _mirror_journal(conn, o: Odoo, entity: str, move_id: int, memo: str, ctx: dict) -> dict | None:
    if not db.load_policy().allowed(ctx["role"], "je.posted"):
        return None
    lines = o.search_read("account.move.line", [("move_id", "=", move_id)],
                          ["account_id", "debit", "credit", "name"])
    je = [{"account": ln["account_id"][1], **({"debit": round(ln["debit"], 2)} if ln["debit"] else
                                               {"credit": round(ln["credit"], 2)})}
          for ln in lines if ln["debit"] or ln["credit"]]
    if len(je) < 2:
        return None
    return db.append(conn, kind="je.posted", entity=f"je:{entity.replace(':', '-')}-{move_id}",
                     payload={"memo": memo, "lines": je, "odoo_move_id": move_id},
                     links=[{"rel": "books", "to": entity}], sources=[f"odoo:account.move/{move_id}"], **ctx)


def post(conn, o: Odoo, entity: str, ctx: dict) -> dict:
    """Confirm a staged draft in Odoo, behind the ledger's posting gate."""
    db.check_post(conn, entity)  # refuses before anything reaches Odoo
    etype = entity.split(":", 1)[0]
    if etype == "billpayment":
        return _pay_bills(conn, o, entity, ctx)
    ref = db.erp_ref(conn, entity)
    if not ref:
        raise OdooError(f"{entity} was never staged; run `stage` first")
    model, rid = ref["model"], ref["id"]
    try:
        if model == "sale.order":
            o.call(model, "action_confirm", [rid])
        elif model == "purchase.order":
            o.call(model, "button_confirm", [rid])
        elif model == "account.move":
            o.call(model, "action_post", [rid])
        else:
            raise OdooError(f"cannot post {model}")
    except OdooError as e:
        db.append(conn, kind="erp.failed", entity=entity, payload={"model": model, "id": rid, "error": str(e)},
                  **ctx)
        raise
    rec = _summary(o, model, rid)
    out = {"posted": db.append(conn, kind="erp.posted", entity=entity, payload=rec,
                               sources=[f"odoo:{model}/{rid}"], **ctx), "odoo": rec}
    if model == "account.move":
        out["journal"] = _mirror_journal(conn, o, entity, rid, f"{rec['name']} for {entity}", ctx)
    return out


def _register(o: Odoo, move_ids: list[int], amount: float, date: str | None, memo: str) -> list[int]:
    """Odoo's Register Payment wizard; returns the new account.payment ids."""
    before = {p["id"] for p in o.search_read("account.payment", [], ["id"], order="id desc", limit=20)}
    ctxw = {"active_model": "account.move", "active_ids": move_ids}
    bank = o.search_read("account.journal", [("type", "=", "bank")], ["id"], limit=1)
    vals: dict[str, Any] = {"amount": round(amount, 2), "communication": memo[:64],
                            "payment_difference_handling": "open", "group_payment": True}
    if bank:
        vals["journal_id"] = bank[0]["id"]
    if date:
        vals["payment_date"] = date
    wiz = o.create("account.payment.register", vals, context=ctxw)
    o.call("account.payment.register", "action_create_payments", [wiz], context=ctxw)
    after = o.search_read("account.payment", [], ["id"], order="id desc", limit=20)
    return sorted(p["id"] for p in after if p["id"] not in before)


def register_payment(conn, o: Odoo, entity: str, applications: list[tuple[str, float]], ctx: dict,
                     date: str | None = None, reason: str = "") -> dict:
    """Apply money received (payment.received in the ledger) to invoices in Odoo."""
    pay = db.latest_payload(conn, entity)
    if pay is None:
        raise OdooError(f"{entity} has no payment.received in the ledger")
    total = round(sum(a for _, a in applications), 2)
    if total > round(float(pay["total"]), 2) + 0.001:
        raise OdooError(f"applying {total} exceeds the {pay['total']} received")
    applied, journals, odoo_ids = [], [], []
    for inv, amount in applications:
        ref = db.erp_ref(conn, inv, posted_only=True)
        if not ref or ref["model"] != "account.move":
            raise OdooError(f"{inv} is not a posted invoice in Odoo")
        ids = _register(o, [ref["id"]], amount, date or pay.get("date"),
                        pay.get("reference") or entity.split(":", 1)[1])
        odoo_ids += ids
        applied.append({"invoice": inv, "amount": amount})
        for pid in ids:
            move = o.read("account.payment", [pid], ["move_id"])[0]["move_id"]
            if move:
                journals.append(_mirror_journal(conn, o, entity, move[0], f"Payment for {inv}", ctx))
    rec = {"model": "account.payment", "id": odoo_ids[0] if odoo_ids else None, "ids": odoo_ids,
           "applications": applied, "reason": reason}
    out = {
        "applied": db.append(conn, kind="payment.applied", entity=entity,
                             payload={"applications": applied, "reason": reason, "odoo_payment_ids": odoo_ids},
                             links=[{"rel": "applies_to", "to": i, "amount": a} for i, a in applications],
                             sources=[f"odoo:account.payment/{i}" for i in odoo_ids], **ctx),
        "journals": [j for j in journals if j],
        "invoices": [_summary(o, "account.move", db.erp_ref(conn, i, posted_only=True)["id"]) for i, _ in applications],
    }
    db.append(conn, kind="erp.posted", entity=entity, payload=rec,
              sources=[f"odoo:account.payment/{i}" for i in odoo_ids], **ctx)
    return out


def _pay_bills(conn, o: Odoo, entity: str, ctx: dict) -> dict:
    pay = db.latest_payload(conn, entity) or {}
    odoo_ids, journals = [], []
    for app in pay.get("applications", []):
        ref = db.erp_ref(conn, app["bill"], posted_only=True)
        if not ref:
            raise OdooError(f"{app['bill']} is not a posted bill")
        ids = _register(o, [ref["id"]], float(app["amount"]), pay.get("date"),
                        pay.get("bank_transfer") or entity.split(":", 1)[1])
        odoo_ids += ids
        for pid in ids:
            move = o.read("account.payment", [pid], ["move_id"])[0]["move_id"]
            if move:
                journals.append(_mirror_journal(conn, o, entity, move[0], f"Payment of {app['bill']}", ctx))
    rec = {"model": "account.payment", "id": odoo_ids[0] if odoo_ids else None, "ids": odoo_ids}
    return {
        "posted": db.append(conn, kind="erp.posted", entity=entity, payload=rec,
                            links=[{"rel": "applies_to", "to": a["bill"], "amount": a["amount"]}
                                   for a in pay.get("applications", [])],
                            sources=[f"odoo:account.payment/{i}" for i in odoo_ids], **ctx),
        "journals": [j for j in journals if j],
    }


# ---------------------------------------------------------------- reads for agents

def three_way(conn, o: Odoo, bill_entity: str) -> dict:
    """PO price and quantity vs received quantity vs billed, line by line."""
    _, po = _posted_parent(conn, o, bill_entity, "from_po")
    bill = db.latest_payload(conn, bill_entity) or {}
    po_lines = o.search_read("purchase.order.line", [("order_id", "=", po["id"])],
                             ["product_id", "product_qty", "qty_received", "qty_invoiced", "price_unit"])
    rows = []
    for pl in po_lines:
        name = pl["product_id"][1] if pl["product_id"] else "?"
        bl = next((b for b in bill.get("lines", []) if b["item"].lower() in name.lower()), None)
        rows.append({
            "item": name, "ordered": pl["product_qty"], "received": pl["qty_received"],
            "po_unit_cost": pl["price_unit"],
            "billed_qty": bl and float(bl.get("qty", 0)), "billed_unit_cost": bl and float(bl["unit_cost"]),
            "qty_over_received": bool(bl) and float(bl.get("qty", 0)) > pl["qty_received"] + 1e-9,
            "price_diff": bl and round(float(bl["unit_cost"]) - pl["price_unit"], 4),
        })
    extra = [b for b in bill.get("lines", []) if not any(b["item"].lower() in r["item"].lower() for r in rows)]
    return {"bill": bill_entity, "po": po.get("name"), "lines": rows, "unmatched_bill_lines": extra}


def simulate_receipt(conn, o: Odoo, po_entity: str, ctx: dict, qty_factor: float = 1.0) -> dict:
    """Simulation only: the vendor's goods arrive and the warehouse validates the receipt.

    qty_factor < 1 simulates a short shipment, which the 3-way match should catch.
    """
    ref = db.erp_ref(conn, po_entity, posted_only=True)
    if not ref:
        raise OdooError(f"{po_entity} is not confirmed in Odoo")
    picks = o.read("purchase.order", [ref["id"]], ["picking_ids"])[0]["picking_ids"]
    done = []
    for pick in o.search_read("stock.picking", [("id", "in", picks), ("state", "not in", ["done", "cancel"])],
                              ["id", "name", "move_ids"]):
        for mv in o.read("stock.move", pick["move_ids"], ["id", "product_uom_qty"]):
            o.write("stock.move", [mv["id"]], {"quantity": round(mv["product_uom_qty"] * qty_factor, 4),
                                               "picked": True})
        res = o.call("stock.picking", "button_validate", [pick["id"]])
        if isinstance(res, dict) and res.get("res_model") == "stock.backorder.confirmation":
            # Short shipment: keep the rest open as a backorder, as a warehouse would.
            wiz_ctx = res.get("context", {})
            wiz = o.create("stock.backorder.confirmation", {}, context=wiz_ctx)
            o.call("stock.backorder.confirmation", "process", [wiz], context=wiz_ctx)
        done.append(pick["name"])
    rec = db.append(conn, kind="sim.receipt", entity=po_entity,
                    payload={"pickings": done, "qty_factor": qty_factor}, **ctx)
    return {"received": done, "event": rec}


TRACKED = {
    "sale.order": ["name", "state", "amount_total", "invoice_status"],
    "purchase.order": ["name", "state", "amount_total", "invoice_status", "receipt_status"],
    "account.move": ["name", "state", "move_type", "amount_total", "amount_residual", "payment_state"],
    "account.payment": ["name", "state", "amount", "partner_id"],
}


def track(conn, o: Odoo, ctx: dict, since: str | None = None) -> dict:
    """Pull what changed in Odoo since the last cursor into erp.changed events."""
    row = conn.execute("SELECT json_extract(payload, '$.cursor') FROM events WHERE entity = 'tracker:odoo'"
                       " ORDER BY seq DESC LIMIT 1").fetchone()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    since = since or (row[0] if row and row[0] else
                      (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"))
    n = 0
    for model, fields in TRACKED.items():
        for rec in o.search_read(model, [("write_date", ">", since)], fields + ["write_date"], order="write_date"):
            entity = db.entity_for_erp(conn, model, rec["id"]) or f"odoo:{model.replace('.', '_')}-{rec['id']}"
            db.append(conn, kind="erp.changed", entity=entity, sources=[f"odoo:{model}/{rec['id']}"],
                      payload={"model": model, **rec}, **ctx)
            n += 1
    db.append(conn, kind="erp.changed", entity="tracker:odoo",
              payload={"cursor": started, "since": since, "changes": n}, **ctx)
    return {"since": since, "changes": n}


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="connectors.odoo", description=__doc__,
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

    sub.add_parser("whoami")
    sub.add_parser("survey")
    sub.add_parser("tb")
    rd = sub.add_parser("read")
    rd.add_argument("model")
    rd.add_argument("domain", help="JSON domain, e.g. '[[\"state\",\"=\",\"sale\"]]'")
    rd.add_argument("fields", nargs="*")
    tw = sub.add_parser("three-way")
    tw.add_argument("entity")
    with_ctx(sub.add_parser("stage")).add_argument("entity")
    with_ctx(sub.add_parser("post")).add_argument("entity")
    rp = with_ctx(sub.add_parser("register-payment"))
    rp.add_argument("entity")
    rp.add_argument("--apply", action="append", required=True, help="invoice:INV-1001=450.00")
    rp.add_argument("--date")
    rp.add_argument("--reason", default="")
    tr = with_ctx(sub.add_parser("track"))
    tr.add_argument("--since")

    args = p.parse_args(argv)
    out = lambda obj: sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")
    try:
        o = Odoo()
        if args.cmd == "whoami":
            c = o.search_read("res.company", [], ["name", "currency_id", "country_id"])
            out({"url": o.url, "db": o.db, "uid": o.uid, "companies": c})
            return 0
        if args.cmd == "survey":
            out(survey(o))
            return 0
        if args.cmd == "tb":
            out(trial_balance(o))
            return 0
        if args.cmd == "read":
            out(o.search_read(args.model, json.loads(args.domain), args.fields or ["display_name"]))
            return 0
        conn = db.connect(Path(args.db) if args.db else None)
        if args.cmd == "three-way":
            out(three_way(conn, o, args.entity))
            return 0
        if not args.actor or not args.role:
            raise db.LedgerError("--actor and --role are required (or LEDGER_ACTOR / LEDGER_ROLE)")
        ctx = {"actor": args.actor, "role": args.role, "run_id": args.run_id, "workflow": args.workflow,
               "workflow_version": args.workflow_version}
        if args.cmd == "stage":
            out(stage(conn, o, args.entity, ctx))
        elif args.cmd == "post":
            out(post(conn, o, args.entity, ctx))
        elif args.cmd == "register-payment":
            apps = []
            for a in args.apply:
                inv, _, amt = a.rpartition("=")
                apps.append((inv, float(amt)))
            out(register_payment(conn, o, args.entity, apps, ctx, args.date, args.reason))
        elif args.cmd == "track":
            out(track(conn, o, ctx, args.since))
        return 0
    except (OdooError, db.LedgerError) as e:
        out({"error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
