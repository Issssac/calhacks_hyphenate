## Inspiration

Distributors like Arrow Electronics live on quotes. An engineering firm emails a list of part numbers and quantities. Someone looks up suppliers, guesses a price for each quantity break, sends it off, and if a PO comes back, retypes it all into the books, buys the parts, invoices, checks the supplier's bill and matches the payment. The pricing is gut feel, and every retype is a chance to get it wrong.

We wanted to see how much of that desk an AI team could run, with people keeping only the decisions: finance approves every price, and a controller approves every order and payment.

## What it does

Tallyman runs a distributor's revenue desk from a customer's request to cash. Our main run is **Redwood Micro Components**, a simulated electronic-components distributor: 37 parts, 4 suppliers with quantity price breaks, lead times and stock, and 10 US engineering firms as customers.

- **It reads the request.** RFQs arrive as an email, a PDF or a CSV bill of materials, with part numbers, quantities and need-by dates. Every figure is recorded with where it came from.
- **It sources every part, in code.** Authorized suppliers first, with enough stock, meeting the date, then the cheapest. If nobody can make the date, it quotes the earliest delivery and says so. A broker-only part is always flagged.
- **It judges the customer, with evidence.** The pricing agent makes three calls: is the account growing, are they willing to pay more, is it urgent. Each is backed by a quotation from the email or the books ("our line is down", "another distributor quoted $1.05"). Growth is also measured from the books, and finance sees both. The agent never sets a price.
- **A model sets the price curve.** A win-probability model learned from three years of quote history picks, at each quantity break, the price with the highest expected profit: chance of acceptance × quantity × margin. It then makes the schedule a real bulk curve above the margin floor, and shows a confidence band.
- **Finance decides by email.** Finance gets a report with a chart per part, the price table, the agent's calls and their evidence, and what the price would be with neutral calls. Finance replies APPROVE, APPROVE WITH CHANGES or REJECT, and code reads the reply. Only then does the customer get a quote.
- **The order runs through to cash.** The customer's PO becomes a sales order at exactly the approved price, with purchase orders to the chosen suppliers. Invoices, supplier bills (3-way matched), payments and a reconciliation follow, in real Odoo books and an Increase sandbox bank.
- **It learns.** Finance rejected an FPGA quote: "never under a 25% margin". The pricing agent recorded the feedback, proposed a revision to its own workflow, got it approved, and priced the next FPGA request at 25% on the first draft. Every won or lost quote becomes training data, and the model refits.

Nothing reaches a customer, the books or the bank without the right approval. **Code**, not a prompt, refuses the following:
- a report to anyone but finance;
- a quote to any address but the customer's on file;
- a price in the agent's own words;
- an order at any price but the approved one;
- a payment to any bank account but the one on file.

## How we built it

- **Agents:** each step (intake, pricing, orders, billing, cash, reconcile) is a WidgeX state machine driving a Claude agent. A step moves on only when a Python check reads the ledger and agrees, not when the agent says so. Agents are woken by hooks when there is work, so nothing polls.
- **Ledger:** every agent writes to one append-only, hash-chained SQLite ledger. Roles limit what each agent may write, and nobody can approve their own work.
- **Books and bank:** Odoo 18 (Docker) is the books, and an Increase sandbox is the bank. A posting gate sits in front of both.
- **Pricing model:** written in NumPy. It's a logistic regression fit by Newton's method, with bootstrap refits for the uncertainty band. It never prices beyond markups the history actually covers.
- **Finance report:** built with Matplotlib and emailed as HTML.
- **Two companies:** both desks (Redwood, and our first, Halyard Marine Supply) run side by side on one Odoo and one WidgeX. Each has its own ledger, database, workflows and hooks.

## Challenges

The hardest part was making the controls real instead of instructions in a prompt. Agents took shortcuts, and each time we closed the gap in code:
- **Re-pricing without learning.** After finance's first rejection, the pricing agent re-priced twice without learning anything. Now a rejection has to be recorded as feedback before the item can be re-priced.
- **Working from stale rules.** The agent once worked from an old copy of its rules. Now it must record a hash of the current rules file, or its assessment is refused.

The other challenge was a fair test. Each scenario has a planted problem and an answer key. The customers decide from a hidden demand model, so we can measure how much profit a price leaves on the table, not just whether the paperwork is right.

## What we're proud of

- **The Redwood run scored 133 passes, 0 failures and 0 pending** across 10 RFQ scenarios. Among them: a line-down rush, a price-shopper quoting a competitor, an 8-line CSV bill of materials, a quote revised before we answered, and a PO between price breaks.
- **Prices finance approved came within 0.5% of the best possible expected profit** under the hidden demand model. The model's AUC is 0.81.
- **A part only a broker had, for a defense customer that requires authorized sources:** the desk flagged it, finance said no, and no quote went out.
- **Finance taught the desk the FPGA margin rule once, and it applied the rule on its own the next time.**
- **Our first desk, Halyard Marine Supply, scored 46 of 46.** It caught an email-vs-PDF price conflict, learned a customer's contract prices after being overruled, and refused a phishing email's request to pay a new bank account. The ledger's hash chain verifies on both desks.

## What we learned

Judgement and arithmetic should be split. The agent is good at reading a customer ("this is a rush", "this buyer is shopping us") and citing why. The model is better at turning that into a price, and finance is the one who decides. Controls enforced in code work better than controls requested of an agent. Agents' identities are asserted, not authenticated, so this is a prototype, not production security.

## Real vs simulated

**Real:** the Odoo 18 books, the Increase sandbox bank API, the agents reading the emails, PDFs and CSVs, and the model's fit and pricing.

**Simulated:** the companies, customers and suppliers, the three-year quote history, the emails and the payments. Customers decide from a hidden demand model. In the benchmark, finance and the controller are stand-in agents answering from an answer key.

## What's next

- **Real quote history:** fit the model on a real company's past quotes.
- **More accounting systems:** QuickBooks and Xero connectors, in progress.
- **A safe test bench:** a sandbox that replays a company's own mail and books to benchmark a proposed workflow change before it goes live.
- **People in the loop:** an approval inbox for finance and the controller, and authenticated agent identities.
