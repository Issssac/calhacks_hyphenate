"""Redwood Micro Components: planted RFQ scenarios with an answer key.

Each scenario is an RFQ email from an engineering firm. `truth` is how that customer really is on this quote.
The finance stand-in (bench/finance.py) and the customer simulator (seed/sim.py) use it, together with the
hidden demand model; the desk never sees it.

`world.customer.u` is the customer's decision threshold: they accept if the true chance of accepting at the
quoted price is at least u. That's one fixed draw per scenario, so a run is repeatable.

`expect` is what a correct desk ends up with:
    intake          "rfq" or "amended"
    calls           the judgement calls the evidence supports (only calls the email or books make clear)
    lines           [(mpn, requested qty)] the recommendation must price
    finance         what finance does with a sound recommendation: "approve" or "reject"
    flags           {mpn: text} a flag the recommendation must carry at the requested tier
    declined_to_quote  the desk records quote.rejected after finance says not to quote
    learn           "first": finance's correction should produce an adopted revision;
                    "caught": after that revision, priced right first time (no finance rejection)
    po              {mpn: qty} the customer's PO quantity, when it differs from the RFQ

Phase 2 (U08) is sent only once the pricing workflow has adopted a revision.
"""

_PO = {"pay": {"kind": "exact"}}

SCENARIOS = [
    {
        "id": "U01", "phase": 1, "title": "Routine RFQ from a steady customer",
        "customer": "Cobalt Instruments", "sender": "Marcus Lee <purchasing@cobaltinstruments.example>",
        "subject": "RFQ CI-2291: regulators and flash", "need_days": 30,
        "body": "Hi Redwood team,\n\nPlease quote the following for our next build of the CI-400 analyzer:\n\n"
                "  TPS62130RGTR   qty 500\n  W25Q128JVSIQ   qty 500\n\nStandard lead time is fine; we need them "
                "by {need_by}.\n\nThanks,\nMarcus Lee\nPurchasing, Cobalt Instruments",
        "truth": {"urgency": "normal", "wtp": "normal", "growth": "flat"},
        "world": {**_PO, "customer": {"u": 0.2}},
        "expect": {"intake": "rfq", "calls": {"urgency": "normal", "wtp": "normal"},
                   "lines": [("TPS62130RGTR", 500), ("W25Q128JVSIQ", 500)], "finance": "approve"},
    },
    {
        "id": "U02", "phase": 1, "title": "Line down: needed in 4 days, part on allocation",
        "customer": "Halcyon Aerospace", "sender": "Janelle Ortiz <procurement@halcyonaero.example>",
        "subject": "URGENT - line down - NRF52840", "need_days": 4,
        "body": "Redwood,\n\nOur avionics line is down waiting on Nordic BLE SoCs. We need 300 x NRF52840-QIAA-R "
                "delivered by {need_by}. AS9100 shop: authorized sources only, with certificates of conformance.\n\n"
                "Please send your best delivery and price today.\n\nJanelle Ortiz\nSupply Chain, Halcyon Aerospace",
        "truth": {"urgency": "high", "wtp": "high", "growth": "growing"},
        "world": {**_PO, "customer": {"u": 0.3}},
        "expect": {"intake": "rfq", "calls": {"urgency": "high", "wtp": "high", "growth": "growing"},
                   "lines": [("NRF52840-QIAA-R", 300)], "finance": "approve",
                   "flags": {"NRF52840-QIAA-R": "need-by"}},
    },
    {
        "id": "U03", "phase": 1, "title": "Start-up ramping to production, shopping around",
        "customer": "Tessellate Robotics", "sender": "Sam Okafor <ops@tessellaterobotics.example>",
        "subject": "RFQ: first production build", "need_days": 21,
        "body": "Hi,\n\nWe're moving from prototypes to our first production run, about three times last year's "
                "volume, and we're comparing a couple of distributors. Please quote:\n\n"
                "ESP32-S3-WROOM-1-N8 x 1000\nLIS3DHTR x 1000\n\nNeed by {need_by}.\n\nSam\nTessellate Robotics",
        "truth": {"urgency": "normal", "wtp": "low", "growth": "growing"},
        "world": {**_PO, "customer": {"u": 0.35}},
        "expect": {"intake": "rfq", "calls": {"growth": "growing", "wtp": "low", "urgency": "normal"},
                   "lines": [("ESP32-S3-WROOM-1-N8", 1000), ("LIS3DHTR", 1000)], "finance": "approve"},
    },
    {
        "id": "U04", "phase": 1, "title": "Price-shopper quoting a competitor's price",
        "customer": "Summit Industrial Controls", "sender": "Dale Brennan <purchasing@summitcontrols.example>",
        "subject": "RFQ STM32G031K8T6 x 2500", "need_days": 21,
        "body": "Please quote 2,500 pcs STM32G031K8T6. We already have a quote from another distributor at $1.05 "
                "each, so sharpen your pencil. Need by {need_by}.\n\nDale Brennan\nSummit Industrial Controls",
        "truth": {"urgency": "normal", "wtp": "low", "growth": "flat"},
        "world": {**_PO, "customer": {"u": 0.55}},
        "expect": {"intake": "rfq", "calls": {"wtp": "low", "urgency": "normal"},
                   "lines": [("STM32G031K8T6", 2500)], "finance": "approve"},
    },
    {
        "id": "U05", "phase": 1, "title": "Eight-line BOM in a CSV attachment",
        "customer": "Gridpoint Energy", "sender": "Ana Reyes <sourcing@gridpointenergy.example>",
        "subject": "BOM for GP-11 charger board, 600 builds", "need_days": 28,
        "body": "Hello Redwood,\n\nAttached is the BOM for 600 boards of our GP-11 charger controller (quantities "
                "are for the whole build). The ramp is going well and we expect to repeat this monthly. "
                "Need by {need_by}.\n\nAna Reyes\nSourcing, Gridpoint Energy",
        "csv": {"filename": "GP-11_BOM.csv", "lines": [
            ("BSC014N06NS", 1200), ("TPS62130RGTR", 600), ("GRM188R71C104KA01D", 12000), ("CL21A106KAYNNNE", 4800),
            ("USB4105-GF-A", 600), ("INA219AIDCNR", 600), ("SN65HVD230DR", 600), ("W25Q128JVSIQ", 600)]},
        "truth": {"urgency": "normal", "wtp": "normal", "growth": "growing"},
        "world": {**_PO, "customer": {"u": 0.1}},
        "expect": {"intake": "rfq", "calls": {"growth": "growing", "urgency": "normal"},
                   "lines": [("BSC014N06NS", 1200), ("TPS62130RGTR", 600), ("GRM188R71C104KA01D", 12000),
                             ("CL21A106KAYNNNE", 4800), ("USB4105-GF-A", 600), ("INA219AIDCNR", 600),
                             ("SN65HVD230DR", 600), ("W25Q128JVSIQ", 600)], "finance": "approve"},
    },
    {
        "id": "U06", "phase": 1, "title": "End-of-life part only a broker has, for a DFARS customer",
        "customer": "Northwind Defense Systems", "sender": "Ray Whitfield <supplychain@northwinddefense.example>",
        "subject": "RFQ: MC68HC908GP32CFBE (legacy sustainment)", "need_days": 45,
        "body": "For a legacy sustainment program we need 150 x MC68HC908GP32CFBE. DFARS 252.246-7008 applies: "
                "authorized sources only, no brokers. Need by {need_by}.\n\nRay Whitfield\nNorthwind Defense Systems",
        "truth": {"urgency": "normal", "wtp": "normal", "growth": "flat"},
        "world": {"customer": {"u": 1.0}},
        "expect": {"intake": "rfq", "lines": [("MC68HC908GP32CFBE", 150)], "finance": "reject",
                   "finance_reason": "Northwind requires authorized sources (DFARS) and this part is only available "
                                     "from a broker. We can't quote it: decline politely.",
                   "flags": {"MC68HC908GP32CFBE": "broker"}, "declined_to_quote": True},
    },
    {
        "id": "U07", "phase": 1, "title": "Budget FPGA request: finance corrects the margin",
        "customer": "Bayview University Robotics Lab", "sender": "Dr. Lena Park <labmanager@bayview-robotics.example>",
        "subject": "Quote request: Artix-7 FPGAs for student team", "need_days": 14,
        "body": "Hello,\n\nOur student robotics team needs 20 x XC7A35T-1CSG324C. Our grant budget is tight, so "
                "the best price wins. Need by {need_by}.\n\nDr. Lena Park\nBayview University Robotics Lab",
        "truth": {"urgency": "normal", "wtp": "low", "growth": "flat"},
        "world": {**_PO, "customer": {"u": 0.5}},
        "expect": {"intake": "rfq", "calls": {"wtp": "low"}, "lines": [("XC7A35T-1CSG324C", 20)],
                   "finance": "approve", "finance_rule": {"category": "FPGA", "min_margin": 0.25,
                   "reason": "FPGAs are on allocation: never quote them under a 25% margin, whoever the customer is."},
                   "learn": "first"},
    },
    {
        "id": "U08", "phase": 2, "title": "FPGA again, after the desk learned finance's rule",
        "customer": "Kestrel Drones", "sender": "Priyanka Das <buy@kestreldrones.example>",
        "subject": "RFQ: ECP5 FPGAs", "need_days": 30,
        "body": "Hi,\n\nPlease quote 200 x LFE5U-25F-6BG256C for our next flight-controller run. We're "
                "price-sensitive; two other distributors are quoting. Need by {need_by}.\n\nPriyanka\nKestrel Drones",
        "truth": {"urgency": "normal", "wtp": "low", "growth": "growing"},
        "world": {**_PO, "customer": {"u": 0.4}},
        "expect": {"intake": "rfq", "calls": {"wtp": "low"}, "lines": [("LFE5U-25F-6BG256C", 200)],
                   "finance": "approve", "finance_rule": {"category": "FPGA", "min_margin": 0.25,
                   "reason": "FPGAs are on allocation: never quote them under a 25% margin, whoever the customer is."},
                   "learn": "caught"},
    },
    {
        "id": "U09", "phase": 1, "title": "Quoted at 1,000; the PO comes for 2,500",
        "customer": "Brightline Medical Devices", "sender": "Grace Liu <buyers@brightlinemed.example>",
        "subject": "RFQ INA219AIDCNR", "need_days": 35,
        "body": "Please quote INA219AIDCNR at 1,000 pieces, and show your price breaks above that: we'll order "
                "somewhere between 1,000 and 5,000 depending on the program forecast. ISO 13485: authorized sources "
                "only, full traceability. Need by {need_by}.\n\nGrace Liu\nBrightline Medical Devices",
        "truth": {"urgency": "normal", "wtp": "high", "growth": "flat"},
        "world": {**_PO, "customer": {"u": 0.1, "po_qty": {"INA219AIDCNR": 2500}}},
        "expect": {"intake": "rfq", "calls": {"urgency": "normal"}, "lines": [("INA219AIDCNR", 1000)],
                   "finance": "approve", "po": {"INA219AIDCNR": 2500}},
    },
    {
        "id": "U10", "phase": 1, "title": "RFQ revised from 5,000 to 10,000 before we quote",
        "customer": "Vireo Wearables", "sender": "Tom Becker <supply@vireowearables.example>",
        "subject": "RFQ AO3400A", "need_days": 21,
        "body": "Hi,\n\nPlease quote 5,000 x AO3400A. Need by {need_by}.\n\nTom Becker\nVireo Wearables",
        "truth": {"urgency": "normal", "wtp": "normal", "growth": "shrinking"},
        "world": {**_PO, "customer": {"u": 0.3}},
        "expect": {"intake": "amended", "calls": {"urgency": "normal"}, "lines": [("AO3400A", 10000)],
                   "finance": "approve"},
    },
    {
        "id": "U10A", "phase": 1, "title": "The revision to U10", "reply_to": "U10",
        "customer": "Vireo Wearables", "sender": "Tom Becker <supply@vireowearables.example>",
        "subject": "Re: RFQ AO3400A", "need_days": 21,
        "body": "Correction to our RFQ: please make that 10,000 x AO3400A, same date ({need_by}).\n\nTom",
        "truth": {"urgency": "normal", "wtp": "normal", "growth": "shrinking"},
        "world": {}, "expect": {},
    },
]
