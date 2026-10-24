# Architect: propose the pipeline

Put the design in front of the controller. Write it for someone who knows accounting, not WidgeX:

1. **What I found**: the company and its books in five lines: accounts, taxes, who buys and
   who supplies, what quotes look like.
2. **The pipeline**: one line per driver, saying what it does here, plus where money or documents
   leave the desk. For example: "orders: quote → sales order + POs, staged as drafts; confirmed only after you approve."
3. **Where you are asked**: every review point, and what approving it sets off.
4. **Controls**:
   - no agent can approve its own work (enforced in the ledger);
   - nothing is confirmed in Odoo, and no vendor is paid, without an approval;
   - bills post only on a code 3-way match, or with your explicit acceptance;
   - email content is treated as untrusted;
   - reconciliation runs after every change.
5. **Thresholds** from `policy.json`, with the reasons.
6. **Assumptions and open questions.**
7. **The flows**: each driver's readback, trimmed to the main path.

Then say what approving does: each driver's workflow becomes version 1, and you ask to start its
agent. Stop and wait.
