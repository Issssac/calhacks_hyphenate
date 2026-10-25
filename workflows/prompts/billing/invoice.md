# Billing: invoice the sales order

1. `python -m ledgerdb next-id invoice INV --actor <you> --role <role>`. Record `invoice.drafted` on `invoice:INV-n` with
   `--link from_so=<so>`. The payload is `{"total": <SO untaxed total>, "due_date": <from terms>, "note": ...}`;
   lines come from the SO.
2. Stage it: `python -m connectors.odoo stage invoice:INV-n --actor billing-agent --role billing --workflow billing`.
   Odoo creates the draft invoice from the confirmed SO, with taxes from its fiscal setup.
3. Check the draft against the SO and the policy: the untaxed amount equals the SO, and the tax is what
   `company_profile.md` says this customer should be charged (exempt, or a rate). If it is right,
   post it: `python -m connectors.odoo post invoice:INV-n ...`. Posting needs the SO to be approved;
   it refuses otherwise.
4. If the draft disagrees and you can't fix it by re-staging, don't post. Record `approval.requested`
   on the invoice describing the problem and the fix you propose, and answer the review question.
