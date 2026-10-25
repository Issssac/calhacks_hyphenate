# Revenue desk: scenario results

Checks: **133 pass**, 0 fail, 0 pending. Ledger hash chain verified: True.

| Scenario | Check | Result | Detail |
|---|---|---|---|
| U01 Routine RFQ from a steady customer | intake: RFQ extracted | pass | quote:Q-1001 |
|  | priced the requested parts and quantities | pass | [('TPS62130RGTR', 500), ('W25Q128JVSIQ', 500)] |
|  | judgement: urgency | pass | normal (expected normal) |
|  | judgement: wtp | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.0% |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | purchasing@cobaltinstruments.example |
|  | customer ordered | pass | CI-PO-4101: 2 line(s) |
|  | order at the approved price | pass | so:SO-1002 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1002 |
|  | vendor bill 3-way matched | pass | 1/1 |
|  | cash applied | pass |  |
| U02 Line down: needed in 4 days, part on allocation | intake: RFQ extracted | pass | quote:Q-1002 |
|  | priced the requested parts and quantities | pass | [('NRF52840-QIAA-R', 300)] |
|  | judgement: urgency | pass | high (expected high) |
|  | judgement: wtp | pass | high (expected high) |
|  | judgement: growth | pass | growing (expected growing) |
|  | flag on NRF52840-QIAA-R: need-by | pass | can't meet the need-by date: earliest delivery 10 days (4 available) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.0% |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | procurement@halcyonaero.example |
|  | customer ordered | pass | HA-PO-4100: 1 line(s) |
|  | order at the approved price | pass | so:SO-1001 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1001 |
|  | vendor bill 3-way matched | pass | 1/1 |
|  | cash applied | pass |  |
| U03 Start-up ramping to production, shopping around | intake: RFQ extracted | pass | quote:Q-1003 |
|  | priced the requested parts and quantities | pass | [('ESP32-S3-WROOM-1-N8', 1000), ('LIS3DHTR', 1000)] |
|  | judgement: growth | pass | growing (expected growing) |
|  | judgement: wtp | pass | low (expected low) |
|  | judgement: urgency | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.0% |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | ops@tessellaterobotics.example |
|  | customer ordered | pass | TR-PO-4102: 2 line(s) |
|  | order at the approved price | pass | so:SO-1003 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1003 |
|  | vendor bill 3-way matched | pass | 1/1 |
|  | cash applied | pass |  |
| U04 Price-shopper quoting a competitor's price | intake: RFQ extracted | pass | quote:Q-1004 |
|  | priced the requested parts and quantities | pass | [('STM32G031K8T6', 2500)] |
|  | judgement: wtp | pass | low (expected low) |
|  | judgement: urgency | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.0% |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | purchasing@summitcontrols.example |
|  | customer declined: outcome learned | pass | lost |
| U05 Eight-line BOM in a CSV attachment | intake: RFQ extracted | pass | quote:Q-1005 |
|  | priced the requested parts and quantities | pass | [('BSC014N06NS', 1200), ('CL21A106KAYNNNE', 4800), ('GRM188R71C104KA01D', 12000), ('INA219AIDCNR', 600), ('SN65HVD230DR', 600), ('TPS62130RGTR', 600), ('USB4105-GF-A', 600), ('W25Q128JVSIQ', 600)] |
|  | judgement: growth | pass | growing (expected growing) |
|  | judgement: urgency | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.0% |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | sourcing@gridpointenergy.example |
|  | customer ordered | pass | GE-PO-4104: 8 line(s) |
|  | order at the approved price | pass | so:SO-1004 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1004 |
|  | vendor bill 3-way matched | pass | 2/2 |
|  | cash applied | pass |  |
| U06 End-of-life part only a broker has, for a DFARS customer | intake: RFQ extracted | pass | quote:Q-1006 |
|  | priced the requested parts and quantities | pass | [('MC68HC908GP32CFBE', 150)] |
|  | flag on MC68HC908GP32CFBE: broker | pass | broker: no authorized source can supply this; needs approval (counterfeit risk) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance rejected, by email | pass | Northwind requires authorized sources (DFARS) and this part is only available from a broker. We can't quote it: decline politely. |
|  | declined to quote (no quote sent) | pass |  |
| U07 Budget FPGA request: finance corrects the margin | intake: RFQ extracted | pass | quote:Q-1007 |
|  | priced the requested parts and quantities | pass | [('XC7A35T-1CSG324C', 20)] |
|  | judgement: wtp | pass | low (expected low) |
|  | report emailed to finance | pass | 3 report(s) |
|  | finance corrected the desk (rule) | pass | FPGAs are on allocation: never quote them under a 25% margin, whoever the custom |
|  | pricing adopted a revision for it | pass |  |
|  | re-priced and approved | pass |  |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | labmanager@bayview-robotics.example |
|  | customer declined: outcome learned | pass | lost |
| U08 FPGA again, after the desk learned finance's rule | intake: RFQ extracted | pass | quote:Q-1010 |
|  | priced the requested parts and quantities | pass | [('LFE5U-25F-6BG256C', 200)] |
|  | judgement: wtp | pass | low (expected low) |
|  | report emailed to finance | pass | 1 report(s) |
|  | priced right first time (finance's rule) | pass | lowest margin 25.0% |
|  | approved without a correction | pass |  |
|  | approved price vs the true optimum | pass | regret 0.0% |
|  | quote sent at the approved prices | pass | buy@kestreldrones.example |
|  | customer declined: outcome learned | pass | lost |
| U09 Quoted at 1,000; the PO comes for 2,500 | intake: RFQ extracted | pass | quote:Q-1008 |
|  | priced the requested parts and quantities | pass | [('INA219AIDCNR', 1000)] |
|  | judgement: urgency | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.4% |
|  | approved price vs the true optimum | pass | regret 0.4% |
|  | quote sent at the approved prices | pass | buyers@brightlinemed.example |
|  | customer ordered | pass | BMD-PO-4105: 1 line(s) |
|  | order at the approved price | pass | so:SO-1005 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1005 |
|  | vendor bill 3-way matched | pass | 1/1 |
|  | cash applied | pass |  |
| U10 RFQ revised from 5,000 to 10,000 before we quote | intake: revision applied | pass | quote:Q-1009 |
|  | priced the requested parts and quantities | pass | [('AO3400A', 10000)] |
|  | judgement: urgency | pass | normal (expected normal) |
|  | report emailed to finance | pass | 1 report(s) |
|  | finance approved, by email | pass | approve |
|  | model's price vs the true optimum | pass | regret 0.5% |
|  | approved price vs the true optimum | pass | regret 0.5% |
|  | quote sent at the approved prices | pass | supply@vireowearables.example |
|  | customer ordered | pass | VW-PO-4106: 1 line(s) |
|  | order at the approved price | pass | so:SO-1006 |
|  | bought from the sourcing rule's supplier | pass | Approved: the customer's PO at the approved quote, from the right sources. |
|  | order confirmed in Odoo | pass |  |
|  | invoiced | pass | invoice:INV-1006 |
|  | vendor bill 3-way matched | pass | 1/1 |
|  | cash applied | pass |  |
| DESK The desk as a whole | model refit on the desk's own outcomes | pass | 3005 -> 3023 quotes |

Ledger head hash: `3d80aa4560ecf5bbf1c4b57fb8f6e99c6b132017b320f6798fad407d671c25d5`. Control code: unchanged since the recorded manifest.

## Real vs simulated

| Source | Events |
|---|---|
| desk agents' own work | 572 |
| controller decisions (a person, recorded by the agent) | 29 |
| code checks behind workflow transitions | 292 |
| simulated world (customers, suppliers, bank deposits) | 52 |
| real Odoo postings and journal mirrors | 237 |
