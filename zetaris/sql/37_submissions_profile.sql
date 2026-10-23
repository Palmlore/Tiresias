-- Discovery (source scout): one registrant's profile and filing history, read straight from
-- its <ticker>_submissions_raw table (11_submissions_table.sql, registered on demand).
-- OUTER keeps the profile row when the filing list is empty.
SELECT cik, name, to_json(tickers) AS tickers, to_json(formerNames) AS former_names,
       accession_number, filings.recent.form[i] AS form,
       filings.recent.filingDate[i] AS filing_date, filings.recent.items[i] AS items
FROM tiresias_sec.{{table}}
LATERAL VIEW OUTER posexplode(filings.recent.accessionNumber) AS i, accession_number
