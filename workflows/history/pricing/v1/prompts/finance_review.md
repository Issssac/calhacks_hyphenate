# Pricing: finance decides

The request was recorded when you sent the report (`python -m pricing.send report`), so there is nothing more to
record here. Finance answers by replying to the report email, and code records their decision as
`approval.granted` or `approval.rejected` on the RFQ, on their behalf.

- Approved (as recommended or with changes): next you send the customer the quote.
- Rejected: their reason is in the note. Next you record it as feedback, and the RFQ comes back to you to re-price.
