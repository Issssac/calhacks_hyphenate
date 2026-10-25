# Reconcile: explain every difference

Take each difference in the latest `recon.result` (`python -m ledgerdb state recon:books`) and find its cause.

- **books** (an account differs between our journal and Odoo): look for Odoo entries with no
  ledger mirror. Use `python -m connectors.odoo read account.move.line '[["account_id.name","ilike","<account>"],["parent_state","=","posted"]]' move_id debit credit date`,
  compare with `python -m ledgerdb query "SELECT * FROM journal WHERE account LIKE '%<account>%'"`, and match
  move ids against `je.posted` payloads (`odoo_move_id`). An entry nobody on the desk made means someone
  booked directly in Odoo. Say who or what if you can tell (the move's `create_uid`, `ref`).
- **unmatched_receipt** (money in that no payment was recorded from): the cash agent hasn't matched it yet,
  or it isn't customer money at all (an opening probe, interest, a refund).
- **bank_tracking** (Increase's balance doesn't equal what we tracked): a transaction we missed, or one tracked twice.

For each difference, record `recon.finding` on `recon:F-n` (`next-id recon F --actor <you> --role <role>`) with
`{"difference": <the item>, "cause": "...", "evidence": [<citations>], "class": "timing|error|external|benign",
"responsible": "<workflow or person or 'outside the desk'>", "fix": "..."}`.

Then act on the class:
- `error` by a desk workflow: record `feedback.recorded` (`type: "mistake"`, `driver: <that workflow>`,
  with the evidence) so that workflow revises itself the next time it runs.
- `benign` (it will never clear on its own, like the sandbox's opening probe deposit): record
  `recon.explained` on the difference's entity, so it stops showing.
- `external` (booked outside the desk): no feedback. It goes to the controller in the report.
- `timing`: nothing. It clears when the work happens.
