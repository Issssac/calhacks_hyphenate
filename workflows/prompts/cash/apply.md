# Cash: match the receipt to invoices

Read the bank transaction: `python -m ledgerdb state <banktxn>`. It gives the amount, the
originator (company name), the entry description, and the addenda (remittance text).

1. **Find the candidates.** List the open invoices:
   `python -m ledgerdb queue cash`, filtered to `open receivable`. For each, `ledgerdb state <invoice>`
   gives the customer and its `erp.posted` payload gives `amount_total`. For the open balance in Odoo,
   use `python -m connectors.odoo read account.move '[["name","=","INV/..."]]' amount_residual payment_state`.
   Check any `remittance` mail from the same customer too (`mail.classified` with class `remittance`).
2. **Decide what it pays**, and say why:
   - **Exact**: one invoice from that customer whose residual equals the amount, or the remittance names it.
   - **Bundled**: several invoices that the remittance names, or whose residuals sum exactly to the amount.
   - **Short**: the remittance or the amount points at an invoice but pays less, e.g. "less damaged goods".
     Apply what was paid, and leave the rest open.
   - **Over or unidentifiable**: you can't tell, or the originator isn't a customer. **Don't guess.**
3. Record the payment: `python -m ledgerdb next-id payment P --actor <you> --role <role>`, then `payment.received` on `payment:P-n`
   with `--link from_bank=<banktxn>`. The payload is
   `{"customer", "total": <amount>, "date", "reference": "<entry description>", "remittance": [...],
   "match": "exact|bundled|short|over|unidentified", "why": "..."}`.
4. Exact, bundled or clearly short: register it in Odoo, one `--apply` per invoice:
   `python -m connectors.odoo register-payment payment:P-n --apply invoice:INV-a=<amount> [--apply ...]
   --reason "<why>" --actor cash-agent --role cash --workflow cash`.
   For a short pay, also record `payment.short` on the payment with
   `{"invoice", "expected", "paid", "difference", "reason": "<from the remittance>"}`. A short pay
   with a dispute reason ("damaged", "not received", "wrong price") goes to the controller as well,
   once it is applied.
5. Over, unidentifiable, or anything else unclear: record `payment.unmatched` with your best reading
   and `approval.requested`, and answer the review question. Register nothing.
