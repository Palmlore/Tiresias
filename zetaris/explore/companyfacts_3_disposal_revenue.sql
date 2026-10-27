-- 3M's revenue tagged as a disposal group. Only 2020 interim rows come back: the Solventum spin-off
-- is not tagged with this concept, so the spun-off revenue has to come from Solventum's own filings.
SELECT period_start, period_end, fp, form, filed, val
FROM tiresias.mmm_disposal_revenue
ORDER BY filed DESC, period_end DESC
LIMIT 10
