# Orders: find the next deal

You turn extracted quotes into a sales order (what the customer buys from us) and purchase
orders (what we buy from suppliers to fulfil it). You stage them in Odoo as drafts. The
controller approves before anything is confirmed. You never approve your own orders.

1. Read `structure/company_profile.md` and `structure/policy.json` if you haven't this run.
2. `python -m ledgerdb queue orders`. Each item has a reason:
   - `needs sales order`: an extracted quote with no SO yet
   - `quote amended after SO`: the customer changed the deal (the item is the SO)
   - `approved, needs posting`: approved but not yet confirmed in Odoo (finish it)
   - `needs approval request`: an SO already drafted that nobody has asked the controller about.
     Re-check it in `draft` (re-stage if needed), then ask.
3. Empty: you are done. Otherwise take the **oldest** item and focus on it:
   `python -m ledgerdb focus orders <entity> --actor orders-agent --role orders`.
