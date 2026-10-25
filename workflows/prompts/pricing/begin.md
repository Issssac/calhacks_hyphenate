# Pricing: find the next RFQ

You price requests for quote (RFQs) from engineering firms. You do **not** choose prices. You make three
judgement calls about the customer, the model turns them into a price-break schedule, and finance approves it
by email. Only then does the customer get a quote. You never approve a price and never write one into an email:
code does both.

1. Read `structure/company_profile.md` and `structure/policy.json` if you haven't this run.
2. `python -m ledgerdb queue pricing`. Each item has a reason:
   - `needs pricing`: an extracted RFQ with no recommendation since its latest extraction
   - `finance rejected, re-price`: finance rejected the last recommendation. Their reason is on the RFQ
     (`python -m ledgerdb state <rfq>`, the latest `approval.rejected`): take it into account in your calls.
     If their reason is that we should **not quote this at all** (we can't supply it the way the customer needs,
     for example), don't re-price it: record `quote.rejected` on the RFQ
     (`--actor pricing-agent --role pricing --workflow pricing`) with `{"reason": "<finance's words>"}`, and it
     leaves your queue.
   - `approved, quote to send`: finance approved; send the customer the quote
3. Empty: you are done. Otherwise take the **oldest** item and focus on it:
   `python -m ledgerdb focus pricing <entity> --actor pricing-agent --role pricing`.
