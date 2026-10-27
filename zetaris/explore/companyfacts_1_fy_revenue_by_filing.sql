-- SEC XBRL company facts (catalog id sec_companyfacts), through the per-concept view: 3M's annual
-- revenue for each fiscal year as each 10-K reported it. A restatement shows as two values for one year.
SELECT period_end, filed, form, val
FROM tiresias.mmm_revenues
WHERE fp = 'FY' AND form = '10-K' AND period_end >= '2021-01-01' AND datediff(period_end, period_start) > 300
ORDER BY period_end, filed
