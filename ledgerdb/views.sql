-- Derived state. Nothing here is stored: every view is computed from events.
-- Views are dropped and recreated on every `init`, so editing this file and
-- re-running init is how they change.

DROP VIEW IF EXISTS doc_state;
DROP VIEW IF EXISTS doc_links;
DROP VIEW IF EXISTS doc_latest;
DROP VIEW IF EXISTS journal;
DROP VIEW IF EXISTS trial_balance;
DROP VIEW IF EXISTS applied;
DROP VIEW IF EXISTS open_items;
DROP VIEW IF EXISTS feedback_open;
DROP VIEW IF EXISTS workflow_current;
DROP VIEW IF EXISTS validations;

-- Latest event per entity, and who first created it.
CREATE VIEW doc_state AS
SELECT e.entity,
       e.entity_type,
       e.kind      AS last_kind,
       e.seq       AS last_seq,
       e.ts        AS last_ts,
       e.actor     AS last_actor,
       (SELECT f.actor FROM events f WHERE f.entity = e.entity ORDER BY f.seq LIMIT 1) AS created_by,
       (SELECT COUNT(*) FROM events f WHERE f.entity = e.entity) AS n_events
FROM events e
WHERE e.seq = (SELECT MAX(seq) FROM events m WHERE m.entity = e.entity);

-- The latest payload that defined each document (drafts, amendments, extractions).
CREATE VIEW doc_latest AS
SELECT e.entity, e.entity_type, e.kind, e.seq, e.payload
FROM events e
WHERE e.seq = (
    SELECT MAX(m.seq) FROM events m
    WHERE m.entity = e.entity
      AND (m.kind LIKE '%.drafted' OR m.kind LIKE '%.amended'
           OR m.kind IN ('quote.extracted', 'quote.clarified', 'bill.received',
                         'payment.received', 'invoice.posted', 'bill.posted'))
);

-- Every link any event declared: quote -> SO -> PO -> invoice/bill -> payment.
CREATE VIEW doc_links AS
SELECT e.seq,
       e.entity                    AS src,
       json_extract(j.value, '$.rel')    AS rel,
       json_extract(j.value, '$.to')     AS dst,
       json_extract(j.value, '$.amount') AS amount,
       e.kind
FROM events e, json_each(e.links) j;

-- Our own double-entry journal, mirrored from the lines the books actually booked.
CREATE VIEW journal AS
SELECT e.seq,
       e.ts,
       e.entity,
       json_extract(e.payload, '$.memo')        AS memo,
       json_extract(j.value, '$.account')       AS account,
       COALESCE(json_extract(j.value, '$.debit'), 0)  AS debit,
       COALESCE(json_extract(j.value, '$.credit'), 0) AS credit
FROM events e, json_each(e.payload, '$.lines') j
WHERE e.kind = 'je.posted';

CREATE VIEW trial_balance AS
SELECT account,
       ROUND(SUM(debit), 2)                AS debit,
       ROUND(SUM(credit), 2)               AS credit,
       ROUND(SUM(debit) - SUM(credit), 2)  AS balance
FROM journal
GROUP BY account
ORDER BY account;

-- Cash applied to each invoice or bill.
CREATE VIEW applied AS
SELECT dst AS target, ROUND(SUM(amount), 2) AS amount
FROM doc_links
WHERE rel = 'applies_to'
GROUP BY dst;

-- Each driver's work queue. A driver drains its queue and stops.
CREATE VIEW open_items AS
-- intake: mail pulled from the inbox that nobody has classified yet
SELECT 'intake' AS queue, s.entity, 'needs classification' AS reason, s.last_seq AS since_seq
FROM doc_state s
WHERE s.entity_type = 'mail'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'mail.classified')
UNION ALL
-- billing: mail classified as a vendor bill that has not been recorded as a bill
SELECT 'billing', s.entity, 'vendor bill to record', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'mail'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'mail.classified'
              AND json_extract(e.payload, '$.class') = 'vendor_bill')
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'from_mail' AND l.dst = s.entity
                  AND l.src LIKE 'bill:%')
UNION ALL
-- cash: money that arrived at the bank and has not been turned into a payment yet
SELECT 'cash', s.entity, 'bank receipt to match', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'banktxn'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'bank.transaction'
              AND json_extract(e.payload, '$.direction') = 'in')
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'from_bank' AND l.dst = s.entity)
UNION ALL
-- intake: quotes received but not yet extracted
SELECT 'intake', s.entity, 'needs extraction', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'quote'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity
                  AND e.kind IN ('quote.extracted', 'quote.clarified', 'quote.rejected', 'quote.duplicate'))
UNION ALL
-- orders: extracted quotes with no sales order yet
SELECT 'orders', s.entity, 'needs sales order', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'quote'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity
              AND e.kind IN ('quote.extracted', 'quote.clarified'))
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind IN ('quote.rejected', 'quote.duplicate'))
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'from_quote' AND l.dst = s.entity)
UNION ALL
-- orders: amended quotes whose SO predates the amendment
SELECT 'orders', l.src, 'quote amended after SO', s.last_seq
FROM doc_links l JOIN doc_state s ON s.entity = l.dst
WHERE l.rel = 'from_quote'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = l.dst AND e.kind = 'quote.amended' AND e.seq > l.seq)
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = l.src AND e.kind = 'so.amended'
                  AND e.seq > (SELECT MAX(a.seq) FROM events a WHERE a.entity = l.dst AND a.kind = 'quote.amended'))
UNION ALL
-- orders: drafted SOs/POs that are approved but not yet confirmed in the books
SELECT CASE s.entity_type WHEN 'billpayment' THEN 'cash' ELSE 'orders' END,
       s.entity, 'approved, needs posting', s.last_seq
FROM doc_state s
WHERE s.entity_type IN ('so', 'po', 'billpayment')
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'approval.granted'
              AND e.seq > (SELECT MAX(d.seq) FROM events d WHERE d.entity = s.entity
                           AND (d.kind LIKE '%.drafted' OR d.kind LIKE '%.amended')))
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted'
                  AND e.seq > (SELECT MAX(d.seq) FROM events d WHERE d.entity = s.entity
                               AND (d.kind LIKE '%.drafted' OR d.kind LIKE '%.amended')))
UNION ALL
-- orders: drafted SOs nobody has asked the controller about (e.g. drafted before a restart)
SELECT 'orders', s.entity, 'needs approval request', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'so'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity
                  AND e.kind IN ('approval.requested', 'approval.granted', 'approval.rejected', 'so.cancelled')
                  AND e.seq > (SELECT MAX(d.seq) FROM events d WHERE d.entity = s.entity
                               AND (d.kind LIKE '%.drafted' OR d.kind LIKE '%.amended')))
UNION ALL
-- billing: posted SOs with no invoice
SELECT 'billing', s.entity, 'needs invoice', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'so'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted')
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'from_so' AND l.dst = s.entity)
UNION ALL
-- billing: posted POs with no bill
SELECT 'billing', s.entity, 'awaiting vendor bill', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'po'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted')
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'from_po' AND l.dst = s.entity)
UNION ALL
-- billing: vendor bills received but not matched and posted
SELECT 'billing', s.entity, 'needs 3-way match', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'bill'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted')
UNION ALL
-- cash: invoices not fully paid
SELECT 'cash', s.entity, 'open receivable', s.last_seq
FROM doc_state s JOIN doc_latest d ON d.entity = s.entity
LEFT JOIN applied a ON a.target = s.entity
WHERE s.entity_type = 'invoice'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted')
  AND COALESCE(a.amount, 0) < ROUND(COALESCE(
        (SELECT json_extract(p.payload, '$.amount_total') FROM events p
         WHERE p.entity = s.entity AND p.kind = 'erp.posted' ORDER BY p.seq DESC LIMIT 1),
        json_extract(d.payload, '$.total')), 2)
UNION ALL
-- cash: bills not fully paid
SELECT 'cash', s.entity, 'open payable', s.last_seq
FROM doc_state s JOIN doc_latest d ON d.entity = s.entity
LEFT JOIN applied a ON a.target = s.entity
WHERE s.entity_type = 'bill'
  AND EXISTS (SELECT 1 FROM events e WHERE e.entity = s.entity AND e.kind = 'erp.posted')
  AND COALESCE(a.amount, 0) < ROUND(COALESCE(
        (SELECT json_extract(p.payload, '$.amount_total') FROM events p
         WHERE p.entity = s.entity AND p.kind = 'erp.posted' ORDER BY p.seq DESC LIMIT 1),
        json_extract(d.payload, '$.total')), 2)
UNION ALL
-- cash: money received that has not been applied to anything
SELECT 'cash', s.entity, 'unapplied cash', s.last_seq
FROM doc_state s
WHERE s.entity_type = 'payment'
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.src = s.entity AND l.rel = 'applies_to');

-- Overrules and mistakes no workflow revision has answered yet.
CREATE VIEW feedback_open AS
SELECT e.seq, e.ts, e.entity, e.actor,
       json_extract(e.payload, '$.driver')  AS driver,
       json_extract(e.payload, '$.state')   AS state,
       json_extract(e.payload, '$.type')    AS type,
       json_extract(e.payload, '$.target')  AS target,
       json_extract(e.payload, '$.note')    AS note
FROM events e
WHERE e.kind = 'feedback.recorded'
  AND NOT EXISTS (SELECT 1 FROM doc_links l WHERE l.rel = 'addresses' AND l.dst = e.entity
                  AND l.kind IN ('workflow.adopted', 'workflow.rejected'));

-- The live version of each workflow.
CREATE VIEW workflow_current AS
SELECT e.entity,
       json_extract(e.payload, '$.version') AS version,
       json_extract(e.payload, '$.sha256')  AS sha256,
       json_extract(e.payload, '$.file')    AS file,
       e.seq, e.ts
FROM events e
WHERE e.kind = 'workflow.adopted'
  AND e.seq = (SELECT MAX(m.seq) FROM events m WHERE m.entity = e.entity AND m.kind = 'workflow.adopted');

-- harness_check results, keyed by file hash so a later edit invalidates them.
CREATE VIEW validations AS
SELECT e.seq, e.entity,
       json_extract(e.payload, '$.file')   AS file,
       json_extract(e.payload, '$.sha256') AS sha256,
       json_extract(e.payload, '$.ok')     AS ok
FROM events e
WHERE e.kind = 'workflow.validated';
