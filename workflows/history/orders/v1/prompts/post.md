# Orders: confirm in Odoo

The controller approved. Their note, if any, is in the message that brought you here. (If you
came straight from `begin`, the approval is already recorded. Skip to step 2.)

1. Record the approval on the person's behalf, once for the SO and once for each PO:
   `python -m ledgerdb append --actor orders-agent --role controller --on-behalf-of <controller from policy.json>
   --kind approval.granted --entity <so or po> --workflow orders --payload @note.json`,
   where the payload is `{"note": "<their note>", "via": "harness review"}`.
2. Confirm each one: `python -m connectors.odoo post <entity> --actor orders-agent --role orders --workflow orders`,
   SO first, then its POs. Posting refuses anything without an approval after its latest draft. If it
   refuses, don't work around it: find out why.
3. Report the Odoo names (SO and POs) and states.
