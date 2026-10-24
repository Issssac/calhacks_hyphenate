"""Set up Halyard Marine Supply's master data in Odoo. Idempotent: safe to run twice.

    python -m seed.company            (uses ODOO_DB, default `revenue`)

Creates no transactions, so the books start empty and every posted entry is the desk's.
"""

from __future__ import annotations

import json
import sys

from connectors.odoo import Odoo
from . import world


def _one(o: Odoo, model: str, domain: list, vals: dict) -> int:
    found = o.search_read(model, domain, ["id"], limit=1)
    if found:
        o.write(model, [found[0]["id"]], vals)
        return found[0]["id"]
    return o.create(model, vals)


def run(o: Odoo) -> dict:
    out: dict = {}
    us = o.search_read("res.country", [("code", "=", "US")], ["id"], limit=1)[0]["id"]
    ca = o.search_read("res.country.state", [("code", "=", "CA"), ("country_id", "=", us)], ["id"], limit=1)[0]["id"]
    c = world.COMPANY
    o.write("res.company", [1], {"name": c["name"], "street": c["street"], "city": c["city"], "zip": c["zip"],
                                 "state_id": ca, "country_id": us, "email": c["email"]})

    # Sales tax, as the company default for products.
    tax = _one(o, "account.tax", [("name", "=", world.SALES_TAX["name"]), ("type_tax_use", "=", "sale")],
               {"name": world.SALES_TAX["name"], "amount": world.SALES_TAX["amount"], "amount_type": "percent",
                "type_tax_use": "sale"})
    o.write("res.company", [1], {"account_sale_tax_id": tax, "account_purchase_tax_id": False})
    exempt_fp = _one(o, "account.fiscal.position", [("name", "=", "Resale exempt")],
                     {"name": "Resale exempt", "tax_ids": [(5, 0, 0), (0, 0, {"tax_src_id": tax, "tax_dest_id": False})]})
    out["tax"] = {"sale_tax_id": tax, "resale_exempt_fiscal_position": exempt_fp}

    vendors = {}
    for v in world.VENDORS:
        pid = _one(o, "res.partner", [("name", "=", v["name"])],
                   {"name": v["name"], "email": v["email"], "is_company": True, "supplier_rank": 1})
        bank = _one(o, "res.bank", [("name", "=", v["bank"]["bank"])],
                    {"name": v["bank"]["bank"], "bic": v["bank"]["routing"]})
        _one(o, "res.partner.bank", [("acc_number", "=", v["bank"]["account"])],
             {"acc_number": v["bank"]["account"], "partner_id": pid, "bank_id": bank})
        vendors[v["name"]] = pid
    out["vendors"] = vendors

    products = {}
    for p in world.PRODUCTS:
        tmpl = _one(o, "product.template", [("name", "=", p["name"])], {
            "name": p["name"], "type": "consu", "list_price": p["list"], "standard_price": p["cost"],
            "sale_ok": True, "purchase_ok": True, "invoice_policy": "order", "purchase_method": "receive",
            "taxes_id": [(6, 0, [tax])], "supplier_taxes_id": [(5, 0, 0)]})
        _one(o, "product.supplierinfo", [("product_tmpl_id", "=", tmpl), ("partner_id", "=", vendors[p["vendor"]])],
             {"product_tmpl_id": tmpl, "partner_id": vendors[p["vendor"]], "price": p["cost"], "min_qty": 1})
        products[p["name"]] = tmpl
    out["products"] = products

    customers = {}
    for cu in world.CUSTOMERS:
        vals = {"name": cu["name"], "email": cu["email"], "is_company": True, "customer_rank": 1,
                "comment": cu["note"], "property_account_position_id": exempt_fp if cu["exempt"] else False}
        pid = _one(o, "res.partner", [("name", "=", cu["name"])], vals)
        if cu.get("contract"):
            items = [(5, 0, 0)] + [(0, 0, {"applied_on": "1_product", "product_tmpl_id": products[name],
                                           "compute_price": "fixed", "fixed_price": price})
                                   for name, price in cu["contract"].items()]
            pl = _one(o, "product.pricelist", [("name", "=", f"{cu['name']} contract")],
                      {"name": f"{cu['name']} contract", "item_ids": items})
            o.write("res.partner", [pid], {"property_product_pricelist": pl})
        customers[cu["name"]] = pid
    out["customers"] = customers
    return out


if __name__ == "__main__":
    sys.stdout.write(json.dumps(run(Odoo()), indent=2) + "\n")
