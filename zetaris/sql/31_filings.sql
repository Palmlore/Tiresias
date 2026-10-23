-- Filings of the given forms for one entity, newest first. {{forms}} is a validated list of
-- quoted form codes, e.g. '10-K', '8-K'.
SELECT accession_number, filing_date, report_date, form, items, primary_document
FROM tiresias.{{ticker}}_filings
WHERE form IN ({{forms}})
ORDER BY filing_date DESC, accession_number DESC
