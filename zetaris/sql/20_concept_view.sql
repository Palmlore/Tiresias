-- One view per (entity, us-gaap concept), reached by struct path. Exploding the whole concept
-- map instead took 18 s per query (AGENTS.md). OR REPLACE makes a re-run safe.
-- Fails, and bootstrap reports "failed", when the entity never reported that concept.
CREATE OR REPLACE SCHEMASTORE VIEW {{ticker}}_{{slug}} WITH CONTAINER tiresias AS
SELECT '{{concept}}' AS concept, 'USD' AS unit, fact.val AS val,
       fact.`start` AS period_start, fact.`end` AS period_end, fact.fy AS fy, fact.fp AS fp,
       fact.form AS form, fact.filed AS filed, fact.accn AS accn, fact.frame AS frame
FROM tiresias_sec.{{ticker}}_facts_raw
LATERAL VIEW explode(facts.`us-gaap`.{{concept}}.units.USD) AS fact
