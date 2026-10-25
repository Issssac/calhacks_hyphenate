# Billing: find the next document

You invoice customers for confirmed sales orders, and record suppliers' bills against our
purchase orders. A bill is posted only when the 3-way match passes (PO vs goods received vs
bill), or when the controller accepts the variance.

1. Read `structure/company_profile.md` and `structure/policy.json` if you haven't this run.
2. Bring in changes from the books: `python -m connectors.odoo track --actor billing-agent --role tracker --workflow billing`
   (tracking writes only `erp.changed`, which is the tracker role's job).
3. `python -m ledgerdb queue billing`. The reasons:
   - `needs invoice`: a confirmed SO (the item is the SO)
   - `vendor bill to record`: an email intake classified as a vendor bill (the item is the mail)
   - `needs 3-way match`: a recorded bill not yet posted
   - `awaiting vendor bill`: a PO with no bill yet. **Not actionable; skip it.**
4. If nothing actionable remains, you are done. Otherwise take the oldest actionable item and focus on it:
   `python -m ledgerdb focus billing <entity> --actor billing-agent --role billing`.
