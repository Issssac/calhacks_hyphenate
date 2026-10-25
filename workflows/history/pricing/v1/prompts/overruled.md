# Pricing: finance rejected the recommendation

Finance's decision is already recorded (code read their reply): the latest `approval.rejected` on the RFQ
(`python -m ledgerdb state <rfq>`). Read their reason carefully. Don't record the decision again.

1. **The item.** You don't fix prices by hand. The RFQ is back in your queue as `finance rejected, re-price`.
   You'll re-assess it there with their reason in mind, and the model re-prices it.
2. **Record the feedback.** Append `feedback.recorded` on a new `feedback:F-n`
   (`python -m ledgerdb next-id feedback F --actor pricing-agent --role pricing`), linked `--link about=<rfq>`:

   ```json
   {"driver": "pricing", "state": "finance_review", "type": "overrule", "target": "<rfq>",
    "note": "<finance's words, verbatim>",
    "what_i_did": "<what you'll do differently on this RFQ>",
    "workflow_version": <from python -m workflows.build current>}
   ```

The next state asks you whether your workflow should change so finance doesn't have to say this again.
