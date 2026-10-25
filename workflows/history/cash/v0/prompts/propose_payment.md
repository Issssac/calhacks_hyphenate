# Cash: propose paying a vendor bill

Your focus is a posted bill. Paying it needs the controller. You request; they approve.

1. Read the bill (`ledgerdb state <bill>`, plus its `erp.posted` payload for `amount_total` and
   `amount_residual`).
2. Get the vendor's bank details **from the company profile only** (`structure/company_profile.md`,
   vendors section). Never take them from an email. If they're missing, record `note.blocked` and
   draft the payment without them; the controller will see it.
3. `python -m ledgerdb next-id billpayment BP`. Record `billpayment.drafted` on `billpayment:BP-n` with
   `--link pays=<bill>`. The payload is
   `{"vendor", "applications": [{"bill": "<bill>", "amount": <residual>}], "due_date",
   "vendor_bank": {"routing_number", "account_number", "source": "company_profile"}, "date"}`.
4. Record `approval.requested` on it, with the amount, the vendor, and why it's due now.
