# Orders: ask the controller to approve

Record `approval.requested` on the SO (if you haven't), with a payload summarising what
follows. Then write the controller a short message:

- **Deal**: customer, their reference, and the SO number staged in Odoo (from the
  `erp.staged` payload, e.g. S00012).
- **Sales order**: one line per item: qty, unit price, line total, citation.
- **Purchase orders**: one line per supplier: items, unit costs, total, and where each cost came from.
- **Margin**: amount and %, against the policy floor.
- **Flags**: anything unusual. A price differing from list or history, a cost guessed from
  standard cost, a line from stock, a check that failed.
- **If approved**: the SO and POs are confirmed in Odoo, and the POs go to the suppliers.

Then stop and wait.
