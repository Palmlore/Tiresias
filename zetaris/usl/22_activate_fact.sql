-- XBRL facts for the concepts case 04 reads, by struct path (never the whole concept map:
-- exploding it took 18 s per query, zetaris/README.md).
ACTIVATE USL TABLE lightning.metastore.tiresias_usl.sec_evidence.fact AS
SELECT COALESCE(CAST(cik AS bigint), 0) AS cik, COALESCE(concept, '') AS concept, 'USD' AS unit,
       COALESCE(CAST(f.val AS double), 0) AS val,
       CAST(f.`start` AS date) AS period_start, COALESCE(CAST(f.`end` AS date), DATE '1900-01-01') AS period_end,
       CAST(f.fy AS int) AS fy, f.fp AS fp, f.form AS form, CAST(f.filed AS date) AS filed,
       COALESCE(f.accn, '') AS accn, f.frame AS frame
FROM (
    SELECT cik, 'Revenues' AS concept, fact AS f FROM tiresias_sec.mmm_facts_raw
    LATERAL VIEW explode(facts.`us-gaap`.Revenues.units.USD) AS fact
    UNION ALL
    SELECT cik, 'NetIncomeLoss' AS concept, fact AS f FROM tiresias_sec.mmm_facts_raw
    LATERAL VIEW explode(facts.`us-gaap`.NetIncomeLoss.units.USD) AS fact
    UNION ALL
    SELECT cik, 'Revenues' AS concept, fact AS f FROM tiresias_sec.solv_facts_raw
    LATERAL VIEW explode(facts.`us-gaap`.Revenues.units.USD) AS fact
) AS facts
