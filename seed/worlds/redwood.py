"""Redwood Micro Components: a small electronic-components distributor in San Jose, CA. (Fictional.)

Engineering firms (aerospace, medical, robotics, energy, defense, labs) email requests for quote
(RFQs) for parts by manufacturer part number (MPN). Redwood finds a supplier for each part, quotes
the customer a price-break schedule, and when the customer sends a purchase order, it buys from the
supplier and resells. Four suppliers: two franchised distributors, one manufacturer-direct channel
(reels and MOQs only, long lead times) and one independent broker (stock on hard-to-find parts, not
an authorized source).

Everything here is what the company itself knows: its catalogue, its suppliers' price breaks, lead
times and stock, and its customers. How customers actually decide (the demand model the benchmark
uses to say yes or no to a quote) is in seed/worlds/redwood_truth.py, which the desk never reads.
"""

from __future__ import annotations

COMPANY = {
    "name": "Redwood Micro Components",
    "street": "2150 Ringwood Ave", "city": "San Jose", "zip": "95131", "state_code": "CA",
    "email": "desk@redwoodmicro.example",
}
DESK_ADDR = "Redwood Micro Components <desk@redwoodmicro.example>"
MSGID_DOMAIN = "sim.redwoodmicro.example"
SCENARIOS_MODULE = "seed.worlds.redwood_scenarios"
# Customers send RFQs; the desk prices them, finance approves the price, the customer may then order.
FLOW = "rfq"
FINANCE = {"name": "Priya Natarajan", "title": "Finance Manager", "email": "finance@redwoodmicro.example"}

# San Jose combined sales rate. Out-of-state shipments carry no California tax.
SALES_TAX = {"name": "CA Sales 9.375%", "amount": 9.375}

VENDORS = [
    {"name": "Avalon Electronics", "email": "ar@avalon-elec.example", "authorized": True,
     "kind": "franchised distributor", "lead_days": 3,
     "bank": {"bank": "Bank of America CA", "routing": "121000358", "account": "5512093374"}},
    {"name": "Keystone Components", "email": "billing@keystonecomp.example", "authorized": True,
     "kind": "franchised distributor", "lead_days": 10,
     "bank": {"bank": "Wells Fargo", "routing": "121000248", "account": "8821004517"}},
    {"name": "Meridian Semiconductor Direct", "email": "receivables@meridiansemi.example", "authorized": True,
     "kind": "manufacturer-direct (reels and MOQs only)", "lead_days": 56,
     "bank": {"bank": "JPMorgan Chase", "routing": "322271627", "account": "3309917742"}},
    {"name": "Pacific Broker Supply", "email": "accounts@pacbroker.example", "authorized": False,
     "kind": "independent broker (not an authorized source)", "lead_days": 2,
     "bank": {"bank": "Silicon Valley Bridge Bank", "routing": "121140399", "account": "6604418820"}},
]

# How each supplier prices: unit cost = the part's base cost x a multiplier at each quantity break.
# `only` limits a supplier to some parts; `min_qty` is its smallest order (reel or MOQ).
SUPPLIER_TIERS = {
    "Avalon Electronics": [(1, 1.00), (10, 0.88), (100, 0.74), (500, 0.68), (1000, 0.64), (5000, 0.61)],
    "Keystone Components": [(1, 1.04), (100, 0.72), (1000, 0.60), (5000, 0.55), (10000, 0.53)],
    "Meridian Semiconductor Direct": [(1000, 0.50), (5000, 0.46), (10000, 0.44)],
    "Pacific Broker Supply": [(1, 1.65), (100, 1.45)],
}

# Parts: MPN, manufacturer, category, description, base cost (one piece from a franchised distributor),
# list price (Redwood's one-piece book price), packaging.
_PARTS = [
    # MCUs and SoCs
    ("STM32F407VGT6", "STMicroelectronics", "MCU", "ARM Cortex-M4 MCU, 1MB flash, LQFP-100", 9.80, 14.20, "tray"),
    ("STM32G031K8T6", "STMicroelectronics", "MCU", "ARM Cortex-M0+ MCU, 64KB flash, LQFP-32", 1.42, 2.10, "tray"),
    ("ATSAMD21G18A-AU", "Microchip", "MCU", "ARM Cortex-M0+ MCU, 256KB flash, TQFP-48", 3.55, 5.20, "tray"),
    ("ESP32-S3-WROOM-1-N8", "Espressif", "MCU", "Wi-Fi/BLE module, 8MB flash", 3.30, 4.90, "reel"),
    ("RP2040", "Raspberry Pi", "MCU", "Dual Cortex-M0+ MCU, QFN-56", 0.82, 1.25, "reel"),
    ("NRF52840-QIAA-R", "Nordic Semiconductor", "MCU", "BLE 5 SoC, aQFN-73", 5.10, 7.40, "reel"),
    ("PIC18F46K22-I/PT", "Microchip", "MCU", "8-bit MCU, 64KB flash, TQFP-44", 3.10, 4.60, "tray"),
    # Power
    ("IRF540NPBF", "Infineon", "MOSFET", "N-ch MOSFET 100V 33A, TO-220", 0.96, 1.45, "tube"),
    ("BSC014N06NS", "Infineon", "MOSFET", "N-ch MOSFET 60V 100A, TDSON-8", 2.35, 3.40, "reel"),
    ("SI2302CDS-T1-GE3", "Vishay", "MOSFET", "N-ch MOSFET 20V 2.6A, SOT-23", 0.21, 0.34, "reel"),
    ("AO3400A", "Alpha & Omega", "MOSFET", "N-ch MOSFET 30V 5.7A, SOT-23", 0.12, 0.20, "reel"),
    ("LM2596S-5.0/NOPB", "Texas Instruments", "Regulator", "5V 3A buck regulator, TO-263", 2.80, 4.10, "tube"),
    ("TPS62130RGTR", "Texas Instruments", "Regulator", "3A step-down converter, VQFN-16", 1.95, 2.90, "reel"),
    ("LT3045EDD#PBF", "Analog Devices", "Regulator", "Ultra-low-noise LDO 500mA, DFN-10", 5.60, 8.10, "tube"),
    ("AMS1117-3.3", "Advanced Monolithic", "Regulator", "3.3V 1A LDO, SOT-223", 0.11, 0.19, "reel"),
    # Passives
    ("GRM188R71C104KA01D", "Murata", "MLCC", "0.1uF 16V X7R 0603 capacitor", 0.010, 0.020, "reel"),
    ("CL21A106KAYNNNE", "Samsung", "MLCC", "10uF 25V X5R 0805 capacitor", 0.045, 0.080, "reel"),
    ("GRM31CR61E226KE15L", "Murata", "MLCC", "22uF 25V X5R 1206 capacitor", 0.150, 0.250, "reel"),
    # Connectors
    ("USB4105-GF-A", "GCT", "Connector", "USB-C receptacle, 16-pin SMT", 0.62, 0.95, "reel"),
    ("10118194-0001LF", "Amphenol", "Connector", "Micro-USB B receptacle, SMT", 0.31, 0.50, "reel"),
    ("53398-0471", "Molex", "Connector", "PicoBlade 4-pin header, 1.25mm", 0.38, 0.60, "reel"),
    ("61300311121", "Wurth", "Connector", "3-pin header 2.54mm, THT", 0.09, 0.16, "bag"),
    # Sensors
    ("BME280", "Bosch", "Sensor", "Temperature, humidity and pressure sensor, LGA-8", 3.20, 4.70, "reel"),
    ("LIS3DHTR", "STMicroelectronics", "Sensor", "3-axis accelerometer, LGA-16", 0.88, 1.35, "reel"),
    ("INA219AIDCNR", "Texas Instruments", "Sensor", "Current/power monitor, SOT-23-8", 1.25, 1.90, "reel"),
    ("TMP117AIDRVR", "Texas Instruments", "Sensor", "0.1C digital temperature sensor, WSON-6", 2.10, 3.10, "reel"),
    # FPGAs
    ("XC7A35T-1CSG324C", "AMD Xilinx", "FPGA", "Artix-7 FPGA, 33K cells, CSBGA-324", 38.50, 54.00, "tray"),
    ("ICE40UP5K-SG48I", "Lattice", "FPGA", "iCE40 UltraPlus FPGA, 5K LUTs, QFN-48", 5.40, 7.90, "tray"),
    ("LFE5U-25F-6BG256C", "Lattice", "FPGA", "ECP5 FPGA, 24K LUTs, CABGA-256", 11.20, 16.40, "tray"),
    # Interface and memory
    ("SN65HVD230DR", "Texas Instruments", "Interface", "3.3V CAN transceiver, SOIC-8", 1.05, 1.60, "reel"),
    ("MAX3232ECDR", "Texas Instruments", "Interface", "RS-232 transceiver, SOIC-16", 0.78, 1.20, "reel"),
    ("TXB0108PWR", "Texas Instruments", "Interface", "8-bit level translator, TSSOP-20", 1.15, 1.75, "reel"),
    ("FT232RL-REEL", "FTDI", "Interface", "USB to UART bridge, SSOP-28", 3.95, 5.80, "reel"),
    ("W25Q128JVSIQ", "Winbond", "Memory", "128Mbit SPI NOR flash, SOIC-8", 1.48, 2.20, "tube"),
    ("24LC256-I/SN", "Microchip", "Memory", "256Kbit I2C EEPROM, SOIC-8", 0.52, 0.80, "tube"),
    ("ATECC608B-SSHDA-T", "Microchip", "Security", "Crypto authentication IC, SOIC-8", 0.74, 1.15, "reel"),
    # End-of-life part: no franchised stock and no factory channel. Only the broker has it.
    ("MC68HC908GP32CFBE", "NXP", "MCU", "8-bit MCU, 32KB flash, QFP-44 (end of life)", 6.20, 15.90, "tray"),
]

# Who carries what, and how many they hold. Default: both franchised distributors stock it; the
# factory channel takes reels/trays of ICs; the broker carries only the hard-to-find parts.
_EOL = {"MC68HC908GP32CFBE"}
_STOCK_OVERRIDES = {
    # A part on allocation: Avalon is out, Keystone has a few hundred at its higher price.
    ("NRF52840-QIAA-R", "Avalon Electronics"): 0,
    ("NRF52840-QIAA-R", "Keystone Components"): 420,
    # The FPGA is scarce everywhere but the factory.
    ("XC7A35T-1CSG324C", "Avalon Electronics"): 36,
    ("XC7A35T-1CSG324C", "Keystone Components"): 0,
}


def _round(x: float) -> float:
    return round(x, 4) if x < 1 else round(x, 3)


def _offers(mpn: str, base: float, category: str) -> list[dict]:
    out = []
    for v in VENDORS:
        name = v["name"]
        if mpn in _EOL and v["authorized"]:
            continue
        if name == "Pacific Broker Supply" and mpn not in _EOL and category not in ("FPGA",):
            continue
        if name == "Meridian Semiconductor Direct" and category in ("Connector",):
            continue
        stock = _STOCK_OVERRIDES.get((mpn, name))
        if stock is None:
            stock = {"Avalon Electronics": 2500, "Keystone Components": 12000,
                     "Meridian Semiconductor Direct": 100000, "Pacific Broker Supply": 300}[name]
        out.append({"vendor": name, "authorized": v["authorized"], "lead_days": v["lead_days"], "stock": stock,
                    "breaks": [(q, _round(base * m)) for q, m in SUPPLIER_TIERS[name]]})
    return out


PRODUCTS = []
for _mpn, _mfr, _cat, _desc, _base, _list, _pack in _PARTS:
    _offs = _offers(_mpn, _base, _cat)
    _auth = [o for o in _offs if o["authorized"]] or _offs
    _primary = min(_auth, key=lambda o: o["breaks"][0][1] if o["breaks"][0][0] == 1 else 1e9)
    PRODUCTS.append({
        "name": _mpn, "mpn": _mpn, "manufacturer": _mfr, "category": _cat, "description": _desc,
        "packaging": _pack, "list": _list,
        # Halyard-style fields, so code written for one supplier still works: the usual source and its
        # one-piece cost.
        "vendor": _primary["vendor"], "cost": _primary["breaks"][0][1],
        "offers": _offs,
    })

CUSTOMERS = [
    {"name": "Halcyon Aerospace", "email": "procurement@halcyonaero.example", "exempt": False,
     "segment": "aerospace", "city": "Long Beach, CA",
     "note": "AS9100 shop: authorized sources only, needs certs of conformance; values lead time over price"},
    {"name": "Brightline Medical Devices", "email": "buyers@brightlinemed.example", "exempt": False,
     "segment": "medical", "city": "Irvine, CA",
     "note": "ISO 13485: authorized sources only, full traceability; steady repeat buyer"},
    {"name": "Tessellate Robotics", "email": "ops@tessellaterobotics.example", "exempt": False,
     "segment": "robotics start-up", "city": "San Francisco, CA",
     "note": "Series A start-up; small but growing builds; watches price closely"},
    {"name": "Northwind Defense Systems", "email": "supplychain@northwinddefense.example", "exempt": False,
     "segment": "defense prime", "city": "San Diego, CA",
     "note": "DFARS: authorized sources only, no brokers; large slow programs"},
    {"name": "Cobalt Instruments", "email": "purchasing@cobaltinstruments.example", "exempt": False,
     "segment": "test & measurement", "city": "Santa Clara, CA", "note": "steady mid-size buyer"},
    {"name": "Bayview University Robotics Lab", "email": "labmanager@bayview-robotics.example", "exempt": True,
     "segment": "university lab", "city": "Berkeley, CA",
     "note": "grant-funded; tax-exempt certificate on file; small quantities"},
    {"name": "Gridpoint Energy", "email": "sourcing@gridpointenergy.example", "exempt": False,
     "segment": "EV charging", "city": "Fremont, CA", "note": "scaling production of charger boards"},
    {"name": "Kestrel Drones", "email": "buy@kestreldrones.example", "exempt": True, "out_of_state": True,
     "segment": "drone start-up", "city": "Austin, TX", "note": "ships to Texas: no California sales tax"},
    {"name": "Vireo Wearables", "email": "supply@vireowearables.example", "exempt": False,
     "segment": "consumer wearables", "city": "Palo Alto, CA", "note": "volumes falling since last year"},
    {"name": "Summit Industrial Controls", "email": "purchasing@summitcontrols.example", "exempt": False,
     "segment": "industrial controls", "city": "Sacramento, CA",
     "note": "shops every RFQ to two or three distributors"},
]
