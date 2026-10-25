# Billing: apply the approved fix

The controller approved your fix (their note is in the message that brought you here).

1. Record `approval.granted` on the invoice, on their behalf (`--role controller --on-behalf-of <controller>`), with their note.
2. Record a new `invoice.drafted` with the corrected payload, re-stage, check, and post.
