# Zetaris

Tiresias runs against a **local Zetaris** (vendor bundle `zetaris-platform`, image tag `2.4.3-v2.4.3.1-hackathon`), installed outside this repository by following the vendor README. REST is at `http://localhost:8888/api/v1.0`.

## What gets registered

`sql/` holds Lightning SQL templates with `{{placeholders}}` and no secrets. One file is one statement; `--` comment lines are stripped before it runs.

| File | Creates | Per |
|---|---|---|
| `00_database.sql` | Lightning database `TIRESIAS_SEC` | once |
| `01_container.sql` | SchemaStore container `tiresias` | once |
| `10_companyfacts_table.sql` | REST table `TIRESIAS_SEC.<ticker>_facts_raw` over SEC companyfacts | entity |
| `11_submissions_table.sql` | REST table `TIRESIAS_SEC.<ticker>_submissions_raw` over SEC submissions | entity |
| `12_cache_table.sql` | `CACHE TABLE` on each raw table, so queries don't refetch the SEC response | raw table |
| `20_concept_view.sql` | View `tiresias.<ticker>_<slug>` via the struct path `facts.us-gaap.<Concept>.units.USD` (no map explode) | entity × concept |
| `21_filings_view.sql` | View `tiresias.<ticker>_filings` (10-K / 8-K history with 8-K item codes) | entity |
| `30_concept_series.sql`, `39_order_facts.sql`, `31_filings.sql`, `32_entities.sql`, `33_views.sql` | Read-only queries the agent runs | call |

Showcase entities: `ge` (CIK 0000040545), `gehc` (0001932393), `gev` (0001996810). Concepts and their view slugs are `CONCEPT_SLUGS` in [`zetaris.py`](../services/agent/src/tiresias_agent/adapters/zetaris.py), the same seven concepts as the offline sample. `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax` are aliases: a revenue query unions whichever of the two views exist.

Placeholders: `{{ticker}}`, `{{cik10}}`, `{{concept}}`, `{{slug}}`, `{{table}}`, `{{forms}}`, and `{{sec_user_agent}}` (from `SEC_USER_AGENT`, required by SEC fair access; masked as `***` in traces). Every value is checked against a strict pattern before it is substituted.

## Apply

```bash
# needs ZETARIS_REST_URL, ZETARIS_ORG_ID, ZETARIS_API_KEY and SEC_USER_AGENT in the environment
cd services/agent
uv run python -m tiresias_agent.adapters.zetaris bootstrap        # one JSON line per statement
uv run python -m tiresias_agent.adapters.zetaris smoke            # entities, revenue facts, filings
uv run python -m tiresias_agent.adapters.zetaris smoke --sample   # same checks, offline
```

`bootstrap` is safe to run twice. Lightning SQL has no `IF NOT EXISTS` for databases, containers or REST tables, so an "already exists" reply is reported as `exists`; views use `CREATE OR REPLACE` and report `replaced`. A view on a concept the entity never reported fails with `FIELD_NOT_FOUND` and is reported as `absent` (for example `gev_goodwill_impairment`); the adapter only queries views that exist. Bootstrap never drops anything. To remove a REST table by hand use `DROP LIGHTNING DATABASE TABLE TIRESIAS_SEC.<table>` (not `DROP TABLE`), and a view with `DROP SCHEMASTORE VIEW <view> FROM tiresias`.

The cache is a session cache: after a Zetaris restart, re-run `bootstrap` so the first query doesn't wait on the SEC fetch.

Loop B (not built yet) is meant to register newly discovered sources from the same templates.
