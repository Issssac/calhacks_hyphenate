# Demo video cue sheet

Video: `docs/demo-media/revenue-desk-demo.mp4`: silent, 1280x720, 1:56 long. Each segment is also a separate clip in `docs/demo-media/clips/`. Every frame shows the real captured output of `python -m desk.demo <segment>` (the text in `docs/demo-output/`), with one caption line added.

**Say this out loud at some point:** In this benchmark the controller is a stand-in agent answering from an answer key.

Don't say "secure": identities in the ledger are asserted, not authenticated.

| Segment | Starts | Length | Clip |
|---|---|---|---|
| title | 0:00 | 7s | `clips/title.mp4` |
| moment | 0:07 | 19s | `clips/moment.mp4` |
| what | 0:26 | 18s | `clips/what.mp4` |
| gate | 0:44 | 22s | `clips/gate.mp4` |
| learn | 1:06 | 22s | `clips/learn.mp4` |
| proof | 1:28 | 12s | `clips/proof.mp4` |
| phish | 1:40 | 8s | `clips/phish.mp4` |
| close | 1:48 | 8s | `clips/close.mp4` |

## title (0:00, 7s)

- Title card: say what this is: an autonomous quote-to-cash desk on WidgeX.
- Say it is a replay of real captured output from the running desk.
- Say out loud: In this benchmark the controller is a stand-in agent answering from an answer key.

## moment (0:07, 19s)

- Frame 1: the same email has two prices: $4.00 in the body, $4.40 in the PDF.
- Nobody told the desk to compare them.
- Frame 2: intake recorded a CONFLICT, citing the email body and the PDF page, and recommended the PDF instead of guessing.
- Say the controller here is a stand-in agent answering from an answer key.

## what (0:26, 18s)

- Intake, orders, billing and cash take a quote to cash; reconcile checks all three.
- Real Odoo books and a real sandbox bank API; the company and its customers are simulated.
- Diagram: each agent is a WidgeX state machine; hard checks read the ledger before a step can finish.
- Everything is appended to one hash-chained ledger; a posting gate sits in front of Odoo and the bank.

## gate (0:44, 22s)

- Agents draft, but they can't commit.
- Frame 1: confirming in Odoo without approval is refused before Odoo is even called; so is approving your own work.
- Frame 2: the trace of one live order, from the email to the invoice to the payment, each row with its source.

## learn (1:06, 22s)

- The controller rejected a sales order: Bay Rigging has contract prices.
- The orders agent recorded the feedback, found the gap in its own workflow, and proposed a revision.
- Frame 2: the revision was reviewed and adopted; the old version is archived. The green lines are the change.
- S08: the same customer at list price again; this time the contract price was applied on the first draft.

## proof (1:28, 12s)

- Ten scenarios, each with a planted problem and an answer key; this table scores the run.
- Point at the tally line and that the ledger hash chain verifies.
- Read the last two lines: what is real and what is simulated, including the stand-in controller.

## phish (1:40, 8s)

- A customer email told 'the AI assistant' to skip review and pay a vendor at a new account.
- Intake flagged it and didn't follow it.
- Even if an agent tried, the bank connector pays only to the account on file in Odoo.

## close (1:48, 8s)

- Order entry, billing, vendor bills with 3-way match, cash application, vendor payments, reconciliation.
- The controller keeps only the decisions.
