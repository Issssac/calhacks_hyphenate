# Pricing: run the model

`python -m pricing.model recommend --rfq <rfq> --actor pricing-agent --role pricing`

This is all code. For every line it finds suppliers by the sourcing rule (authorized sources first, enough stock,
the need-by date), fits or loads the win-probability model, and picks the price at each quantity tier that
maximizes expected profit. It then makes the schedule a bulk curve above the margin floor and records
`pricing.recommended`. Read what it printed:

- `lines[].schedule`: price, margin, chance of winning and source at each tier; `flags` on a tier say when
  a broker is the only source, the need-by date can't be met, or the price hit the history's limit;
- `unsourced`: parts nothing can supply. Say so in the report; don't drop them;
- `growth_disagrees`: your growth call and the books disagree. That's fine if your evidence is good;
  finance will see both.

If it refuses (for example "the assessment can't be used"), fix the assessment in `assess` terms and run it again.
Never edit the recommendation or work out prices yourself.
