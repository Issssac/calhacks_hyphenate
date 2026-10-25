"""Screenshot the real Odoo UI for the demo's records (headless Edge via Playwright, 1280x720).

    python docs/demo-media/odoo_ui.py <repo with connectors/> <out dir>

Logs in from code with the desk's Odoo settings (ODOO_URL/DB/USER/PASSWORD, as connectors/odoo.py
does) and hands the session cookie to the headless browser. Nothing is typed into a login form.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

PAGES = [
    ("01-sales-order-S00005", "/odoo/action-sale.action_orders/5"),
    ("02-purchase-order-P00007", "/odoo/action-purchase.purchase_form_action/7"),
    ("03-customer-invoice-INV-2026-00004", "/odoo/action-account.action_move_out_invoice_type/14"),
    ("04-vendor-bill-BILL-2026-10-0005", "/odoo/action-account.action_move_in_invoice_type/16"),
    ("05-customer-payment", "/odoo/action-account.action_account_payments/8"),
    ("06-journal-items", "/odoo/action-account.action_account_moves_all"),
    ("07-sales-orders-list", "/odoo/action-sale.action_orders"),
    ("08-vendor-bills-list", "/odoo/action-account.action_move_in_invoice_type"),
]


def main() -> None:
    repo, out = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(repo))
    from connectors import env  # noqa: F401  (loads .env)
    from connectors.odoo import Odoo

    o = Odoo()
    s = requests.Session()
    r = s.post(o.url + "/web/session/authenticate",
               json={"jsonrpc": "2.0", "params": {"db": o.db, "login": o.user, "password": o.password}}, timeout=20)
    if not r.json().get("result", {}).get("uid"):
        raise SystemExit("Odoo login failed")
    sid = s.cookies.get("session_id")
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        ctx.add_cookies([{"name": "session_id", "value": sid, "domain": urlparse(o.url).hostname, "path": "/"}])
        page = ctx.new_page()
        for name, path in PAGES:
            # Odoo keeps a long-poll open, so wait for the view itself rather than network idle.
            page.goto(o.url + path, wait_until="load")
            page.wait_for_selector(".o_form_view, .o_list_view, .o_kanban_view", timeout=30000)
            time.sleep(2.5)
            page.screenshot(path=str(out / f"{name}.png"))
            print(name, page.title())
        b.close()


if __name__ == "__main__":
    main()
