# Cash: find the next item

You turn money that lands at the bank into payments against the right invoices. You propose
vendor payments for posted bills, and send them only after the controller approves. Realised
cash is the end of the revenue flow.

1. Read `structure/company_profile.md` and `structure/policy.json` if you haven't this run.
2. Bring in the bank and the books:
   `python -m connectors.increase track --actor cash-agent --role tracker --workflow cash` and
   `python -m connectors.odoo track --actor cash-agent --role tracker --workflow cash`.
3. `python -m ledgerdb queue cash`. The reasons:
   - `bank receipt to match`: money in at the bank (the item is `banktxn:`)
   - `unapplied cash`: a payment recorded but not applied
   - `approved, needs posting`: an approved vendor payment not yet sent
   - `open payable`: a posted bill. Actionable only if no payment has been proposed for it yet.
   - `open receivable`: an invoice not fully paid. **Not actionable.** It is what receipts match against.
4. If nothing actionable remains, you are done. Otherwise take the oldest actionable item and focus on it:
   `python -m ledgerdb focus cash <entity> --actor cash-agent --role cash`.
