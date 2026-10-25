# Orders: draft and stage the SO and its POs

Start from the quote's latest extraction: `python -m ledgerdb state <quote>`, using the latest
`quote.clarified`, `quote.amended` or `quote.extracted`.

**Sales order.** `python -m ledgerdb next-id so SO --actor <you> --role <role>`. Payload (see `connectors/odoo.py`):
`{"customer", "customer_email", "customer_ref", "lines": [{"item", "description", "qty",
"unit_price", "source"}], "total"}`. Prices and quantities come from the quote, each line keeping
its `source`. Record `so.drafted` with `--link from_quote=<quote>`. For an amendment, first check whether
the existing SO has been confirmed (`python -m ledgerdb state <so>` shows an `erp.posted`, or
`python -m connectors.odoo read sale.order '[["name","=","<S000nn>"]]' state invoice_status`).
If it is still a draft, record `so.amended` on it with the whole revised order. If it is
**already confirmed** (and especially if invoiced or paid), do **not** touch it: it cannot be
restaged and rewriting its ledger record leaves it disagreeing with Odoo. Instead draft a new
add-on SO holding only the added or increased lines (the difference in quantity, at the quote's
unit price), with `--link from_quote=<quote> --link amends=<so>`, and a matching add-on PO for
the extra quantity. Say in the approval request that it is a delta, that its total will not tie
to the amended quote total, and what the whole order now comes to.

**Purchase orders.** Group lines by supplier and draft one PO per supplier. Find each line's
cost and supplier in this order:
1. the quote itself (`unit_cost`, `supplier`);
2. the product's vendor prices in Odoo: `python -m connectors.odoo read product.supplierinfo
   '[["product_tmpl_id.name","ilike","<item>"]]' partner_id price min_qty`;
3. the product's standard cost (`product.product`, `standard_price`), with the supplier from
   the company profile.

Say which you used in each line's `source`. A line is only `"from_stock": true` when we really
hold stock of it (we normally hold none). If you find **no** cost and no supplier for a line,
and the item is not in the Odoo catalogue, do **not** draft or stage a sales order for it:
nothing can be bought to fulfil it and the margin cannot be checked. Instead record
`approval.requested` on the quote (`quote:Q-n`) listing the flags (no cost, no supplier, not in
catalogue) and recommending that we decline and tell the customer we do not supply it, then
answer the "breaks a rule you have flagged" question. Only draft the SO if the controller
then names a supplier and cost. For a line that is in the catalogue but has no cost, set
`"from_stock": true` and flag it as before. Record `po.drafted` on
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
