# Cash: send the approved vendor payment

1. If you came from the review, record `approval.granted` on the bill payment on the
   controller's behalf, with their note. (From `begin`, it is already recorded.)
2. Send it: `python -m connectors.increase send billpayment:BP-n --actor cash-agent --role cash --workflow cash`.
   This refuses without the approval.
3. Register it in the books: `python -m connectors.odoo post billpayment:BP-n --actor cash-agent --role cash --workflow cash`.
4. Report the ACH transfer id and the bill's new state.
