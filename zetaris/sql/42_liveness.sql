-- Liveness probe before a live run: a constant query, no table and no SEC fetch, that still
-- needs a running SparkContext (a stopped one fails it in ~0.1 s; adapters/zetaris.py LivenessProbe).
SELECT 1 AS alive
