-- Which fiscal years of 3M revenue were reported with more than one value across 10-Ks.
SELECT period_end, COUNT(DISTINCT val) AS distinct_values, MIN(val) AS low, MAX(val) AS high, COUNT(*) AS reports
FROM tiresias.mmm_revenues
WHERE fp = 'FY' AND form = '10-K' AND datediff(period_end, period_start) > 300
GROUP BY period_end
HAVING COUNT(DISTINCT val) > 1
ORDER BY period_end
