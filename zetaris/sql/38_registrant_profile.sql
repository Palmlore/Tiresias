-- Discovery (scope check, entity directory): one registrant's industry code and its annual
-- report periods, read from its <ticker>_submissions_raw table (11_submissions_table.sql).
-- sic feeds same-industry peers; report_date of consecutive 10-Ks gives a fiscal year.
-- OUTER keeps the profile row when the filing list is empty.
SELECT cik, name, sic, to_json(tickers) AS tickers, to_json(formerNames) AS former_names,
       accession_number, filings.recent.form[i] AS form,
       filings.recent.filingDate[i] AS filing_date, filings.recent.items[i] AS items,
       filings.recent.reportDate[i] AS report_date
FROM tiresias_sec.{{table}}
LATERAL VIEW OUTER posexplode(filings.recent.accessionNumber) AS i, accession_number
