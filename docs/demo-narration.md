# Demo narration (about 2 minutes)

Run `python -m desk.demo` in the repo, terminal font 24pt+, window about 110 columns wide.
Press Enter between segments. Each segment can also be run alone, e.g. `python -m desk.demo gate`.
Before recording, run `python -m desk.demo --check`. It says whether the self-correction segment
("learn") can be recorded yet.

| Time | Segment | Command | Needs |
|---|---|---|---|
| 0:00 | The moment | `moment` | ready now |
| 0:15 | What it is | `what` + open `docs/architecture.html` | ready now |
| 0:35 | The gate | `gate` | ready now |
| 1:05 | Self-correction | `learn` | **the current run must finish the S04 lesson** (see `--check`) |
| 1:40 | Proof | `proof` | ready now (the numbers grow as the run finishes) |
| 1:55 | Close | `close` | ready now |
| optional | Payment redirect refused | `phish` | ready now |

---

## 0:00 The moment

> A customer emails to confirm 200 shackles at four dollars each. The PDF attached to the same
> email says four forty. Nobody told our desk to compare them. Its intake agent read both, recorded a
> conflict citing the email body and the PDF page, and recommended the PDF, since it's the formal
> acceptance, instead of guessing. The controller agreed. In this benchmark, the controller is a
> stand-in agent answering from an answer key.

## 0:15 What it is

> This is an autonomous revenue desk for a simulated marine-hardware distributor. Five agents take a
> quote to cash in real Odoo books and a real sandbox bank: intake, orders, billing and cash, with
> reconcile checking all three. Each agent works inside a workflow that checks every step in code.
> Every action goes into one tamper-evident ledger.

## 0:35 The gate

> Agents draft, but they can't commit. Here an agent drafts a sales order and tries to confirm it in
> Odoo without approval, and the code refuses before Odoo is even called. It tries to approve its own
> order, and that's refused too. In the live run, every number traces back to its source: this trace
> walks from the posted order back to the email and the PDF page it came from.

## 1:05 Self-correction (record only when `--check` says it's ready)

> When the controller overrules the desk, the desk changes how it works. Here the controller rejected a
> sales order: the customer has contract prices. The orders agent recorded why, diagnosed the gap in
> its own workflow, and proposed a revision. The revision was reviewed and adopted, and the old version
> was archived. The next time the same customer quoted at list price, the desk applied the contract
> price on its first draft, with no correction needed.

## 1:40 Proof

> Every scenario has a planted problem and an answer key, and this table scores the run against it.
> What's real: the Odoo books, the sandbox bank's API, and the agents reading the emails and PDFs.
> What's simulated: the company, its customers and suppliers, the emails, the payments, and in this
> benchmark, the controller.

## 1:55 Close

> It fills the revenue desk's seats: order entry, billing, vendor bills, cash application, vendor
> payments and reconciliation. The controller keeps only the decisions.

## Optional: the payment redirect

> One customer email told "the AI assistant" to skip review and pay a vendor at a new bank account.
> Intake flagged it and didn't follow it. Even if an agent had tried, the bank connector pays only to
> the account on file in Odoo's vendor master, and it refuses.

---

### Don't say

- "secure": identities are asserted, not authenticated (README, Limits).
- That a person approved anything in the benchmark: the controller is a stand-in agent.
