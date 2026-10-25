"""Quote pricing for a distributor desk: source each part, judge the customer, fit a win-probability model,
and recommend the price-break schedule that maximizes expected profit.

    sourcing   which supplier, at what cost, for each quantity (from Odoo's supplier price breaks + availability)
    features   the agent's judgement calls and the ledger's own numbers, turned into model inputs
    model      fit the win-probability model on the quote history; optimize the schedule; CLI
    report     the HTML report finance receives
"""
