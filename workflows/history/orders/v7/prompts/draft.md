# Orders: draft and stage the SO and its POs

Start from the quote's latest extraction: `python -m ledgerdb state <quote>`, using the latest
`quote.clarified`, `quote.amended` or `quote.extracted`.

**Sales order.** `python -m ledgerdb next-id so SO --actor <you> --role <role>`. Payload (see `connectors/odoo.py`):
`{"customer", "customer_email", "customer_ref", "lines": [{"item", "description", "qty",
"unit_price", "source"}], "total"}`. Prices and quantities come from the quote, each line keeping
its `source`. Record `so.drafted` with `--link from_quote=<quote>`. For an amendment, record
`so.amended` on the existing SO with the whole revised order.

**Purchase orders.** Group lines by supplier and draft one PO per supplier. Find each line's
cost and supplier in this order:
1. the quote itself (`unit_cost`, `supplier`);
2. the product's vendor prices in Odoo: `python -m connectors.odoo read product.supplierinfo
   '[["product_tmpl_id.name","ilike","<item>"]]' partner_id price min_qty`;
3. the product's standard cost (`product.product`, `standard_price`), with the supplier from
   the company profile.

Say which you used in each line's `source`. If you find **no** cost, or a line is filled from
stock, set `"from_stock": true` on the SO line and flag it. Record `po.drafted` on
`po:PO-n` (`next-id po PO --actor <you> --role <role>`) with `--link for_so=<so>`.

**Stage them**: `python -m connectors.odoo stage <entity> --actor orders-agent --role orders --workflow orders`,
first the SO, then each PO. Staging creates the draft quotation or RFQ in Odoo.

**Check your work** before answering. The code check will verify that:
- the SO lines sum to the SO total, and the total ties to the quote;
- every SO line is covered by a PO line, or flagged `from_stock`;
- no line costs more than it sells for;
- the margin `(SO total - PO totals) / SO total` is at or above `margin_floor` in the policy;
- everything is staged since its latest draft.

If any of these fails and you can't fix it from the material, do not force it. Answer the
"breaks a rule you have flagged" question, after recording an `approval.requested` event on the
SO whose payload lists the flags.
