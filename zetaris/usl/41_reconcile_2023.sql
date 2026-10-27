-- Case 04 in one statement: 3M's FY2023 revenue as first reported and as restated, next to
-- Solventum's own FY2023 revenue. The agent computes the same add-back in Python from per-company views.
SELECT original.val AS mmm_original, restated.val AS mmm_restated, spun.val AS solventum,
       restated.val + spun.val AS add_back, restated.val + spun.val - original.val AS residual
FROM (SELECT val FROM lightning.metastore.tiresias_usl.sec_evidence.fact WHERE cik = 66740 AND concept = 'Revenues' AND fp = 'FY'
        AND period_start = DATE '2023-01-01' AND period_end = DATE '2023-12-31' AND filed = DATE '2024-02-07') AS original
CROSS JOIN (SELECT val FROM lightning.metastore.tiresias_usl.sec_evidence.fact WHERE cik = 66740 AND concept = 'Revenues' AND fp = 'FY'
        AND period_start = DATE '2023-01-01' AND period_end = DATE '2023-12-31' AND filed = DATE '2025-02-05') AS restated
CROSS JOIN (SELECT MIN(val) AS val FROM lightning.metastore.tiresias_usl.sec_evidence.fact f JOIN lightning.metastore.tiresias_usl.sec_evidence.company c ON f.cik = c.cik
        WHERE c.entity_name = 'Solventum Corp' AND f.concept = 'Revenues' AND f.fp = 'FY'
        AND f.period_start = DATE '2023-01-01' AND f.period_end = DATE '2023-12-31') AS spun
