-- Each fact joined to the filing that reported it. "untraced" facts cite a filing outside SEC's
-- recent filing list: the rows behind the fact.accn foreign-key DQ failures.
SELECT c.entity_name, x.fy, CASE WHEN f.accession_number IS NULL THEN 'untraced' ELSE 'traced' END AS source_filing,
       COUNT(*) AS facts, MIN(x.filed) AS first_filed, MAX(x.filed) AS last_filed
FROM lightning.metastore.tiresias_usl.sec_evidence.fact x JOIN lightning.metastore.tiresias_usl.sec_evidence.company c ON x.cik = c.cik
LEFT JOIN lightning.metastore.tiresias_usl.sec_evidence.filing f ON x.accn = f.accession_number AND x.cik = f.cik
GROUP BY c.entity_name, x.fy, CASE WHEN f.accession_number IS NULL THEN 'untraced' ELSE 'traced' END
ORDER BY c.entity_name, x.fy, source_filing
