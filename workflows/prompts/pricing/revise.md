# Pricing: revise the workflow after finance's correction

Read `_tail/revise.md` (in the prompts folder) and follow it. What is specific to pricing:

**What you can't change:** how prices are computed. `pricing/model.py`, `pricing/sourcing.py` and
`pricing/features.py` are control code. The manifest refuses a promotion if they changed, and the agent never
sets a price.

**What you can change**, so the same correction isn't needed again:
- **Your judgement rules**, in `workflows/proposed/prompts/pricing/assess.md`. For example: "A defense prime's
  RFQ is never `wtp: low` on a competitor's price alone."
- **Policy, add-only**, in `workflows/proposed/policy.pricing.json`. The model honours these keys:
  - `margin_floor_by_category`: `{"FPGA": 0.25}`: no part of that catalogue category is quoted under that margin;
  - `margin_floor_by_customer`: `{"<customer>": 0.20}`;
  - `contract_prices`: `{"<customer>": {"<MPN>": <unit price>}}`: a fixed price, not modelled.

  Add each with a `"why"` entry that quotes finance.

If finance's reason was about this one RFQ only (for example "they already accepted a competitor"), no workflow
change is needed: record `workflow.rejected` answering the feedback, as `_tail/revise.md` says.
