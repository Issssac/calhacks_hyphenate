# Architect: design the desk for this company

If you were sent back from `propose`, the controller's note is in the message that brought you
here. Address it first.

Write three files:

**`structure/company_profile.md`**: what every driver reads before it works. Plain prose and tables:
- *Company*: what it sells, to whom, and how deals arrive.
- *Books*: account mapping (revenue, COGS, AR, AP, tax, bank) with Odoo account codes; taxes, and
  which customers are taxable or exempt; payment terms.
- *Customers*: name, email domain, terms, tax status, any contract or special prices you saw.
- *Vendors*: name, what we buy from them, price list, terms, **bank routing and account number
  from Odoo** (cash pays vendors from here and only here).
- *Quote patterns*: what quotes look like in this inbox, and where price and cost appear.
- *For the intake / orders / billing / cash / reconcile desk*: a short section each, with
  company-specific guidance (e.g. "Pier 39 always sends a PDF and a summary in the body; the PDF governs").
- *Open questions*: what you couldn't determine.

**`structure/policy.json`**: the numbers the checks use. Start from `ledgerdb/policy.py`
(`DEFAULT_POLICY`) and set them to fit what you saw:
`{"margin_floor", "price_tolerance_pct", "price_tolerance_abs", "total_tie_abs", "controller",
"idle_minutes"}`. Add a `"why"` object explaining each value.

**`structure/pipeline.json`**: which drivers this company needs, and how to run them:

```json
{"company": "...", "controller": "human:controller",
 "drivers": [
   {"name": "intake",    "enabled": true, "model": "claude-sonnet-5-5", "token_budget": 400000, "why": "..."},
   {"name": "orders",    "enabled": true, "model": "claude-sonnet-5-5", "token_budget": 400000, "why": "..."},
   {"name": "billing",   "enabled": true, "model": "claude-sonnet-5-5", "token_budget": 400000, "why": "..."},
   {"name": "cash",      "enabled": true, "model": "claude-sonnet-5-5", "token_budget": 400000, "why": "..."},
   {"name": "reconcile", "enabled": true, "model": "claude-haiku-5-5",  "token_budget": 200000, "why": "..."}],
 "review_points": ["<where a person is asked, and why>"],
 "assumptions": ["..."]}
```

Disable a driver only when this company truly has no such step, and say why. Record a
`structure.design` event summarising the three files.
