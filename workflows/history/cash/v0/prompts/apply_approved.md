# Cash: carry out the approved application

The controller approved (their note is in the message that brought you here). Record
`approval.granted` on the payment, on their behalf, with their note. Then do what was approved:
register the payment as recommended (or as their note adjusts it) with `connectors.odoo register-payment`.
If what was approved is to hold the money as unapplied, record `payment.unmatched` with
`"held": true` and their note instead.
