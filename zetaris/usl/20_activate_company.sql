-- One row per company from SEC submissions. cik arrives as a string there and as a number in
-- companyfacts, so both sides are cast to bigint. NOT NULL columns need COALESCE: activation
-- rejects a nullable expression for a NOT NULL column ("Schema mismatch").
ACTIVATE USL TABLE lightning.metastore.tiresias_usl.sec_evidence.company AS
SELECT COALESCE(CAST(cik AS bigint), 0) AS cik, COALESCE(name, '') AS entity_name, sic,
       `sicDescription` AS sic_description, `fiscalYearEnd` AS fiscal_year_end
FROM (
    SELECT cik, name, sic, `sicDescription`, `fiscalYearEnd` FROM tiresias_sec.mmm_submissions_raw
    UNION ALL
    SELECT cik, name, sic, `sicDescription`, `fiscalYearEnd` FROM tiresias_sec.solv_submissions_raw
) AS companies
