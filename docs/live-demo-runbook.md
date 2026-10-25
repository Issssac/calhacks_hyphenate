# Live demo runbook (record on the desk machine, one take)

The desk only runs on Andy's machine. Everything below happens there. Worktree:
`C:\Users\Andy\.widgex\.xylonActiveCosms\calhacks_hyphenate-widgex-research-and-design-1792880091` (below: "the worktree").

Choose one plan:

- **Plan A, safe (about 10 minutes of setup):** the finished run. All 46 checks pass. Show the real records in Odoo, the
  WidgeX workflows, and the demo script running live in a terminal. Nothing gets reset.
- **Plan B, a fresh run (about 15 minutes, plus waiting):** reset the world, drop new quotes, and film them flowing through
  the desk into Odoo. It's live, but it can stall, and the first order takes about 5 minutes to reach Odoo. Record it in
  pieces and cut.

## Pre-flight (both plans, 5 minutes)

Open a terminal in the worktree (font 24pt or larger, window about 110 columns) and run:

```
docker compose up -d                      # Odoo + Postgres
python -m connectors.odoo whoami          # should print the Halyard Marine Supply company
python -m desk.demo --check               # should end "learn is ready to record"
python -m eval.score                      # should end {'pass': 46} ... ledger verified: True
```

Check that the forwarder is running. It wakes the agents.
`tail -3 /tmp/forwarder.log` should show a line from the last minute. If it doesn't, start it:

```
nohup python -m desk.forwarder run --every 30 >> /tmp/forwarder.log 2>&1 &
```

For Plan B, the world simulator also has to be running. It sends the vendor bills and customer payments:

```
nohup bash -c 'for i in $(seq 1 90); do python -m seed.sim step >> /tmp/sim_loop.log 2>&1; sleep 120; done' >/dev/null 2>&1 &
```

## Windows to arrange

1. **WidgeX, Workflows page:** the six agents, `revenue-desk-intake`, `-orders`, `-billing`, `-cash`, `-reconcile` and
   `-controller`, each showing its current state.
2. **A browser tab on Odoo:** http://localhost:8069. Log in as admin (local sandbox). Open Sales > Orders.
3. **The terminal** from the pre-flight.

## Plan A: the finished run (about 2 minutes on camera)

| Time | Show | Do | Point out |
|---|---|---|---|
| 0:00 | Terminal | `python -m desk.demo moment` | The email says $4.00 and the PDF says $4.40. The desk recorded a conflict instead of guessing. The controller is a stand-in agent answering from an answer key. |
| 0:20 | Odoo | Sales > Orders > **S00005** | The real order in the books at the PDF price, 200 x $4.40 = $880. The note at the bottom says `ledger so:SO-1005`. |
| 0:35 | WidgeX | The Workflows page; click into `revenue-desk-orders` | Each agent is a workflow, a state machine. Python checks decide when a step may finish. |
| 0:50 | Terminal | `python -m desk.demo gate` | Posting without approval, or approving your own work, is refused in code. Then the trace from email to payment. |
| 1:15 | Terminal | `python -m desk.demo learn` | Overruled on contract pricing, the orders agent rewrote its own workflow (v10 -> v11). The next order from the same customer was right the first time. |
| 1:40 | Odoo | Purchase > **P00007**; Accounting > Vendors > Bills > **BILL/2026/10/0005** (PAID) | The supplier side: PO, matched bill, paid only to the bank account on file. |
| 1:50 | Terminal | `python -m desk.demo proof` | 46 pass, 0 fail, against a planted answer key. What's real and what's simulated. |

## Plan B: a fresh run

1. `python -m bench.reset`. This archives the current run to `~/.revenue-desk/runs/<time>/`, rebuilds Odoo, re-seeds
   the company, and keeps the adopted workflow versions. It takes about 2 to 3 minutes. **After this, Plan A's records
   (S00005 and so on) are gone from Odoo.** They stay in the archive only. Do Plan A first if you want both.
2. Start recording. Show the empty Sales > Orders list in Odoo.
3. `python -m seed.sim quotes --phase 1` drops the ten scenario quotes (emails and PDFs) into the inbox.
4. What happens next, with timings from the last run:
   - **0:30:** the forwarder sees the mail and wakes intake. The intake agent's state changes on the Workflows page.
   - **1:00:** the first quote has been extracted.
   - **3:00:** S03's email/PDF price conflict has been recorded and goes to the controller.
   - **2:00 to 5:00:** orders drafts the sales orders and asks for approval. The controller decides within about 30 to 60 seconds.
   - **5:00:** the first sales order appears in Odoo. Refresh Sales > Orders.
   - **6:00:** the first invoice is posted.
   - Payments and vendor bills arrive over the next 30 to 60 minutes as the simulator runs.
5. Cut the footage to the moments listed above. Narrate the waits, or skip them.

Approvals in this run go through the ledger: the controller agent records its decision, and you see it in the trace and
in the agents' progress. There are no review cards. Switching to card approvals means recompiling and re-promoting every
workflow, and the controller seat then needs a person. Don't attempt that on the night.

## Known risks

- **The forwarder stops after a WidgeX reload.** No agent moves. Check `tail /tmp/forwarder.log` and restart it (pre-flight).
- **The simulator stops after a reload.** Bills and payments never arrive. Restart it.
- **An agent rests "waiting for a person"** and the hook can't wake it: open its Card tab and press Resume (or Run again).
  Typing into its chat gives it one turn, but its workflow doesn't move.
- **The controller takes 30 to 60 seconds** per decision (one forwarder cycle plus its own turn).
- **Hook tokens** live in `~/.revenue-desk/hooks.json`. If a hook answers 401, the tokens need minting again. Don't
  show that file on screen.
- **Odoo's tax label** on S00005 reads "CA Sales 10.75%" while the total row says "Tax 15%". That's how the seeded tax is
  named in Odoo, not a desk error. The amount, $94.60, is 10.75%.
- **Don't say "secure".** Identities in the ledger are asserted, not authenticated.
