-- Facts for one concept view. For a concept with aliases (Revenues and
-- RevenueFromContractWithCustomerExcludingAssessedTax) the adapter joins one SELECT per
-- registered alias view with UNION ALL, then appends 39_order_facts.sql.
SELECT concept, unit, val, period_start, period_end, fy, fp, form, filed, accn, frame
FROM tiresias.{{ticker}}_{{slug}}
