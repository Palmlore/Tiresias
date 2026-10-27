-- Filings that sit in both companies' histories: the rows behind the filing primary-key DQ
-- failures. They link parent and spin-off (3M's ownership filings on its retained Solventum stake).
SELECT f.accession_number, f.form, f.filing_date, concat_ws(' + ', sort_array(collect_set(c.entity_name))) AS companies
FROM lightning.metastore.tiresias_usl.sec_evidence.filing f JOIN lightning.metastore.tiresias_usl.sec_evidence.company c ON f.cik = c.cik
GROUP BY f.accession_number, f.form, f.filing_date
HAVING COUNT(*) > 1
ORDER BY f.filing_date
