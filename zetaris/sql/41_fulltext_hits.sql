-- Discovery (source scout): the filers in one full-text search page, most hits first. A hit is
-- one document (a filing's main document or an exhibit) whose text contains the phrase; a
-- filing with several filers counts for each.
SELECT cik, first(h._source.display_names[i]) AS name, count(*) AS hits,
       min(h._source.file_date) AS first_date, max(h._source.file_date) AS last_date,
       concat_ws(',', sort_array(collect_set(h._source.form))) AS forms,
       concat_ws(',', sort_array(array_distinct(flatten(collect_list(h._source.items))))) AS items,
       max(hits.total.value) AS total
FROM tiresias_sec.{{table}}
LATERAL VIEW explode(hits.hits) AS h
LATERAL VIEW posexplode(h._source.ciks) AS i, cik
GROUP BY cik
ORDER BY hits DESC, cik DESC
LIMIT {{limit}}
