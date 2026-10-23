-- One view per entity over the filing history. SEC returns parallel arrays under
-- filings.recent, so posexplode one of them and index the others by position.
CREATE OR REPLACE SCHEMASTORE VIEW {{ticker}}_filings WITH CONTAINER tiresias AS
SELECT accession_number,
       filings.recent.filingDate[i] AS filing_date,
       filings.recent.reportDate[i] AS report_date,
       filings.recent.form[i] AS form,
       filings.recent.items[i] AS items,
       filings.recent.primaryDocument[i] AS primary_document
FROM tiresias_sec.{{ticker}}_submissions_raw
LATERAL VIEW posexplode(filings.recent.accessionNumber) AS i, accession_number
