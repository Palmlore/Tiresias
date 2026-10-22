# Zetaris

Tiresias runs against a **local Zetaris** (vendor bundle `zetaris-platform`, image tag `2.4.3-v2.4.3.1-hackathon`), installed outside this repository by following the vendor README. REST is at `http://localhost:8888/api/v1.0`.

`sql/` will hold the Lightning SQL that registers the catalog sources for the showcase entities (`CREATE LIGHTNING DATABASE`, `CREATE LIGHTNING REST TABLE`, flattening views). Run it one statement at a time. Sources discovered in Loop B are registered by the agent at runtime from the same templates.
