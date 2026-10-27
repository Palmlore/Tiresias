-- Filing history, the same posexplode over SEC's parallel arrays as zetaris/sql/21_filings_view.sql.
-- filings.recent holds the most recent filings only (at least a year, or 1,000 filings).
ACTIVATE USL TABLE lightning.metastore.tiresias_usl.sec_evidence.filing AS
SELECT COALESCE(accession_number, '') AS accession_number, COALESCE(CAST(cik AS bigint), 0) AS cik, form,
       CAST(filing_date AS date) AS filing_date, CAST(NULLIF(report_date, '') AS date) AS report_date, items
FROM (
    SELECT cik, accession_number, filings.recent.form[i] AS form, filings.recent.filingDate[i] AS filing_date,
           filings.recent.reportDate[i] AS report_date, filings.recent.items[i] AS items
    FROM tiresias_sec.mmm_submissions_raw
    LATERAL VIEW posexplode(filings.recent.accessionNumber) AS i, accession_number
    UNION ALL
    SELECT cik, accession_number, filings.recent.form[i] AS form, filings.recent.filingDate[i] AS filing_date,
           filings.recent.reportDate[i] AS report_date, filings.recent.items[i] AS items
    FROM tiresias_sec.solv_submissions_raw
    LATERAL VIEW posexplode(filings.recent.accessionNumber) AS i, accession_number
) AS filings
