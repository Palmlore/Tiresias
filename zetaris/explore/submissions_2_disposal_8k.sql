-- 8-Ks with item 2.01 (completion of an acquisition or disposition), the item a completed spin-off files.
SELECT accession_number, filing_date, items, primary_document
FROM tiresias.mmm_filings
WHERE form = '8-K' AND items LIKE '%2.01%'
ORDER BY filing_date DESC
