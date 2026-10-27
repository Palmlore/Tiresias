-- The USL is active: every table answers, across both companies.
SELECT 'company' AS usl_table, COUNT(*) AS rows FROM lightning.metastore.tiresias_usl.sec_evidence.company
UNION ALL SELECT 'filing', COUNT(*) FROM lightning.metastore.tiresias_usl.sec_evidence.filing
UNION ALL SELECT 'fact', COUNT(*) FROM lightning.metastore.tiresias_usl.sec_evidence.fact
