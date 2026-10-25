# Billing: post the bill

If you came here from the review, the controller accepted the variance. Record
`approval.granted` on the bill on their behalf, with their note. If you came from a clean
match, no approval is needed.

Post it: `python -m connectors.odoo post bill:B-n --actor billing-agent --role billing --workflow billing`.
The bill is now payable; the cash desk proposes the payment.
