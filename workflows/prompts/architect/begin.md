# Architect: investigate how this company keeps its books

You design the revenue desk for this company. Before you design anything, find out how its
books, bank and inbox actually work. Don't assume; look. Run tools from the repository root.

1. **The books.** `python -m connectors.odoo survey`. Save the output to `data/scratch/survey.json`,
   then read it. Work out:
   - which accounts revenue, cost of goods, receivables, payables, sales tax and the bank post to;
   - which taxes exist and which are defaults; payment terms;
   - products: list prices, standard costs, vendor price lists (`vendor_prices`), invoicing policy
     (ordered vs delivered) and purchase policy (ordered vs received);
   - customers and vendors, their terms, and **vendor bank accounts on file** (`bank_accounts`);
   - anything already open (draft or confirmed orders).
2. **The bank.** `python -m connectors.increase balance`, and the account in `~/.revenue-desk/bank.json`.
   Note the current balance as the opening position.
3. **The inbox.** `python -m connectors.gmail peek -n 15`: a read-only sample. If Gmail isn't
   connected, look at the `.eml` files waiting in the drop folder (`python -m connectors.inbox where`)
   and at any `mail.received` already in the ledger. What do quotes look
   like here: PDF tables, prose in the body, forwarded threads? Who sends them? Do they state our
   cost and supplier, or only the customer's price? Are vendor bills and remittances arriving too?
4. **Record what you found**, one event per topic, on `structure:<topic>`:
   `structure.accounts`, `structure.taxes`, `structure.products`, `structure.parties`, `structure.bank`,
   `structure.inbox`, `structure.controls`. Each payload holds the facts plus `"sources"`
   (`odoo:<model>/<id>`, `gmail:<id>`). Use `--actor architect-agent --role architect --workflow architect`.

Flag anything that would break the standard flow. For example: products invoiced on delivery but
no stock app, vendors with no bank details, taxes that look wrong.
