# Redwood Micro Components: company profile

(Written by the lead from the Odoo survey of the `redwood` database, standing in for an architect run.)

## Company
Redwood Micro Components (USD, San Jose, CA) is a small distributor of electronic components: MCUs, power parts,
passives, connectors, sensors, FPGAs, interface and memory ICs. Engineering firms (aerospace, medical devices,
robotics, EV charging, defense, test and measurement, university labs) email **requests for quote (RFQs)** by
manufacturer part number (MPN) and quantity, sometimes with a need-by date or a BOM attachment (CSV). We hold no
stock: we quote a price-break schedule, and when the customer sends a PO we buy from a supplier and resell.

Finance approves every price before it is quoted: Priya Natarajan, finance@redwoodmicro.example.

## Books (Odoo 18, generic chart)
| Purpose | Account |
|---|---|
| Revenue | 400000 Product Sales |
| Cost of goods | 500000 Cost of Goods Sold |
| Receivable | 121000 Account Receivable |
| Payable | 211000 Account Payable |
| Sales tax collected | 251000 Tax Received |
| Bank | 101401 Bank (journal BNK1); 101403 Outstanding Receipts, 101404 Outstanding Payments |
| Journals | INV customer invoices, BILL vendor bills |

Taxes: **CA Sales 9.375%** (tax id 3) on sales, set on every product. Never use the generic 15% taxes (ids 1, 2).
Purchases carry no tax. Fiscal positions: **Resale exempt** (id 1: Bayview University Robotics Lab) and
**Out of state (no CA tax)** (id 2: Kestrel Drones, Austin TX). Both map CA Sales 9.375% to no tax and must be
applied on those customers' orders and invoices. No contract prices are on file. Payment terms default to 30 days.

## Customers
| Name | Email | Notes |
|---|---|---|
| Halcyon Aerospace | procurement@halcyonaero.example | AS9100: authorized sources only, certs of conformance |
| Brightline Medical Devices | buyers@brightlinemed.example | ISO 13485: authorized sources only, traceability |
| Tessellate Robotics | ops@tessellaterobotics.example | start-up, price-sensitive |
| Northwind Defense Systems | supplychain@northwinddefense.example | DFARS: authorized sources only, never brokers |
| Cobalt Instruments | purchasing@cobaltinstruments.example | steady |
| Bayview University Robotics Lab | labmanager@bayview-robotics.example | tax-exempt (Resale exempt fiscal position) |
| Gridpoint Energy | sourcing@gridpointenergy.example | scaling production |
| Kestrel Drones | buy@kestreldrones.example | out of state: no CA tax |
| Vireo Wearables | supply@vireowearables.example | volumes falling |
| Summit Industrial Controls | purchasing@summitcontrols.example | shops every RFQ |

## Suppliers
| Name | Kind | Typical lead | Bank (Odoo) |
|---|---|---|---|
| Avalon Electronics | franchised distributor, authorized | 3 days | Bank of America CA, routing 121000358, acct 5512093374 |
| Keystone Components | franchised distributor, authorized | 10 days | Wells Fargo, routing 121000248, acct 8821004517 |
| Meridian Semiconductor Direct | manufacturer-direct, authorized, MOQ 1,000 | 56 days | JPMorgan Chase, routing 322271627, acct 3309917742 |
| Pacific Broker Supply | independent broker, **not authorized** | 2 days | Silicon Valley Bridge Bank, routing 121140399, acct 6604418820 |

Each part has supplier price breaks (`product.supplierinfo`: `min_qty`, `price`, `delay` in days), about 500 rows
in all. The sourcing rule is code (`python -m pricing.sourcing choose`): authorized sources only, enough stock, meets
the need-by date, cheapest at that quantity. A broker is used only when nothing authorized can supply, and that is
always flagged.

## Quote history
`<desk home>/pricing/history.csv`: about 3,000 past quotes over three years (customer, part, quantity, cost, price
quoted, the sales rep's notes on urgency, willingness to pay and growth, won or lost). **Simulated** for this
benchmark. The pricing model learns from it and from every quote the desk sends.

## For the desk
- **Intake:** an RFQ has part numbers and quantities but no prices. Extract it with `"rfq": true`, one line per MPN, the
  need-by date, and verbatim `signals` (urgency, competitor prices, "authorized sources only"). Read CSV BOMs from
  the attachment file. A reply with a PO accepts our quote: record `quote.accepted`. A "no thanks" is `quote.declined`.
- **Pricing:** three evidenced judgement calls, then the model, then finance by email, then the customer quote.
- **Orders:** an RFQ becomes an order only on the customer's PO, at the quoted price for that quantity; suppliers
  from the sourcing rule. Customers in aerospace, medical and defense never get broker parts.
- **Billing:** invoice on order with tax 3, except the two fiscal positions above; 3-way match supplier bills
  (component costs run to fractions of a cent: tolerance per policy).
- **Cash:** pay suppliers only to the accounts above, after approval. Apply inbound ACH to invoices.
- **Reconcile:** ledger vs Odoo vs bank; report, never fix silently.

## Open questions
- Real quote history (this one is simulated).
- Who besides finance may approve prices (only finance@ for now).
