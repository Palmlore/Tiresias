-- Discovery (source scout): how many filings matched one full-text search. Run before
-- 41_fulltext_hits.sql: with no hit, Zetaris infers hits.hits as array<string> and the
-- per-hit query cannot run.
SELECT hits.total.value AS total FROM tiresias_sec.{{table}}
