-- The spun-off company's profile and first filings, from its own submissions table.
SELECT name, sic, `sicDescription` AS sic_description, to_json(tickers) AS tickers,
       filings.recent.form[size(filings.recent.form) - 1] AS first_form,
       filings.recent.filingDate[size(filings.recent.filingDate) - 1] AS first_filing_date
FROM tiresias_sec.solv_submissions_raw
