-- Hits grouped by filer: the spun-off company stands out (same shape as zetaris/sql/41_fulltext_hits.sql).
SELECT cik, first(h._source.display_names[i]) AS name, count(*) AS hits,
       min(h._source.file_date) AS first_date, max(h._source.file_date) AS last_date
FROM tiresias_sec.fts_d014334df6b92aed_raw
LATERAL VIEW explode(hits.hits) AS h
LATERAL VIEW posexplode(h._source.ciks) AS i, cik
GROUP BY cik
ORDER BY hits DESC, cik DESC
LIMIT 5
