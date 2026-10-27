-- SEC submissions (catalog id sec_submissions): 3M's recent filings by form.
SELECT form, COUNT(*) AS filings, MIN(filing_date) AS first, MAX(filing_date) AS last
FROM tiresias.mmm_filings
GROUP BY form
ORDER BY filings DESC
LIMIT 12
