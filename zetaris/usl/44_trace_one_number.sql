-- One number traced end to end: Solventum's FY2023 revenue, the filing that reported it and
-- the raw source it was activated from.
SELECT c.entity_name, c.cik, x.concept, x.val, x.period_start, x.period_end, x.accn,
       f.form AS filing_form, f.filing_date, f.report_date,
       'tiresias_sec.solv_facts_raw' AS raw_table,
       concat('https://data.sec.gov/api/xbrl/companyfacts/CIK', lpad(CAST(c.cik AS string), 10, '0'), '.json') AS sec_source
FROM lightning.metastore.tiresias_usl.sec_evidence.fact x JOIN lightning.metastore.tiresias_usl.sec_evidence.company c ON x.cik = c.cik
LEFT JOIN lightning.metastore.tiresias_usl.sec_evidence.filing f ON x.accn = f.accession_number AND x.cik = f.cik
WHERE c.entity_name = 'Solventum Corp' AND x.concept = 'Revenues' AND x.fp = 'FY'
  AND x.period_start = DATE '2023-01-01' AND x.period_end = DATE '2023-12-31'
ORDER BY x.filed
