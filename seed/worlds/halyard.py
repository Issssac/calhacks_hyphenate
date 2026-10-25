"""Halyard Marine Supply: a small marine-hardware distributor in Alameda, CA.

It buys hardware, rope and electronics from three suppliers and resells them to
boatyards and charter fleets. Quotes come in by email, as PDFs and as prose.
"""

COMPANY = {
    "name": "Halyard Marine Supply",
    "street": "1 Sandbox Pier", "city": "Alameda", "zip": "94501", "state_code": "CA",
    "email": "desk@halyard.example",
}
DESK_ADDR = "Halyard Marine Supply <desk@halyard.example>"
MSGID_DOMAIN = "sim.halyard.example"
SCENARIOS_MODULE = "seed.worlds.halyard_scenarios"
# How a customer's request becomes an order: here the customer sends a quote they have accepted.
FLOW = "accepted_quote"

# Sales tax: Alameda County combined rate. One customer holds a resale certificate.
SALES_TAX = {"name": "CA Sales 10.75%", "amount": 10.75}

VENDORS = [
    {"name": "Harbor Forge", "email": "ar@harborforge.example",
     "bank": {"bank": "Bank of America CA", "routing": "121000358", "account": "4401928811"}},
    {"name": "Pacific Cordage", "email": "billing@pacificcordage.example",
     "bank": {"bank": "Wells Fargo", "routing": "121000248", "account": "7730015520"}},
    {"name": "BlueWater Electronics", "email": "invoices@bluewater.example",
     "bank": {"bank": "JPMorgan Chase", "routing": "322271627", "account": "9182736450"}},
]

PRODUCTS = [
    # name, list price, (vendor, vendor price)
    {"name": "Galvanized Shackle 1/2in", "list": 5.00, "vendor": "Harbor Forge", "cost": 3.10},
    {"name": "Stainless Shackle 3/8in", "list": 7.25, "vendor": "Harbor Forge", "cost": 4.80},
    {"name": "Bronze Thru-Hull 1in", "list": 38.00, "vendor": "Harbor Forge", "cost": 24.50},
    {"name": "Double-Braid Line 1/2in (ft)", "list": 1.90, "vendor": "Pacific Cordage", "cost": 1.15},
    {"name": "Three-Strand Nylon 5/8in (ft)", "list": 1.35, "vendor": "Pacific Cordage", "cost": 0.78},
    {"name": "Bilge Pump 1100GPH", "list": 89.00, "vendor": "BlueWater Electronics", "cost": 61.00},
    {"name": "VHF Radio DSC", "list": 249.00, "vendor": "BlueWater Electronics", "cost": 182.00},
]

CUSTOMERS = [
    {"name": "Pier 39 Boatworks", "email": "purchasing@pier39boatworks.example", "exempt": True,
     "note": "resale certificate on file; always sends a PDF quotation"},
    {"name": "Sausalito Yacht Services", "email": "ops@sausalitoyacht.example", "exempt": False,
     "note": "orders by short email"},
    # Bay Rigging's contract was signed on paper and never loaded into Odoo. Only the controller
    # knows it, which is exactly the kind of knowledge the desk has to learn from being corrected.
    {"name": "Bay Rigging Co", "email": "buyer@bayrigging.example", "exempt": False,
     "contract": {"Galvanized Shackle 1/2in": 4.50, "Double-Braid Line 1/2in (ft)": 1.70},
     "contract_in_odoo": False, "note": "rigging shop"},
    {"name": "Delta Charter Fleet", "email": "ap@deltacharter.example", "exempt": False,
     "note": "pays by ACH, sometimes nets deductions"},
]
