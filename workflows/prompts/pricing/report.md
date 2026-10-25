# Pricing: send finance the report

`python -m pricing.send report --rfq <rfq> --actor pricing-agent --role pricing`

This renders the latest recommendation as the finance report: a chart per part, the full table, your three
calls with their evidence, and how to reply. It emails the report to finance (`policy.finance_approvers`; code
refuses any other address) and records `approval.requested` on the RFQ with `review_state` `finance_review`.

Then you're done with this RFQ until finance answers. Their reply is read in code. You'll be started again
with their decision (approve, approve with changes, or reject), so there is nothing to wait for.
