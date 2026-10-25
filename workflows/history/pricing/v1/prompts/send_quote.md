# Pricing: send the customer the approved quote

`python -m pricing.send quote --rfq <rfq> --actor pricing-agent --role quoting --workflow pricing [--note "<one short, friendly line>"]`

Code writes the email and the PDF from finance's approval: every tier's price exactly as approved, with lead
times and a 30-day validity. It sends only to the customer's email on file in Odoo and only if finance approved
the **latest** recommendation, then records `quote.sent`. Use the `quoting` role for this command and the
`pricing` role for everything else.

The optional note is the only text you add. It must not contain prices or amounts; code refuses one that does.
Use it for something useful to the buyer, e.g. "The 1,000 tier ships from the factory in about 8 weeks."

If it refuses, read the reason: no approval, or no email on file. Never send a quote any other way.
