-- Which forms the hits are: a Form 10 registration is the spin-off's signature.
SELECT h._source.form AS form, COUNT(*) AS hits
FROM tiresias_sec.fts_d014334df6b92aed_raw
LATERAL VIEW explode(hits.hits) AS h
GROUP BY h._source.form
ORDER BY hits DESC
