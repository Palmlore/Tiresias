# Data

`sample/` holds the offline slice used by `SAMPLE_MODE=true`: trimmed SEC EDGAR responses for the GE family (GE, GE HealthCare, GE Vernova) and for 3M and Solventum (the lead example, `input_examples/04`), plus one cached EDGAR full-text search. 676,366 bytes in 13 files (452,223 in 8 before 3M/Solventum were added on 2026-10-24). Sources, licences and entities are in [docs/data-sources.md](../docs/data-sources.md).

| File | Content |
|---|---|
| `<entity>_companyfacts.json` | XBRL company facts, same structure as the SEC response, kept to 7 core income-statement concepts (all restated values kept) |
| `<entity>_submissions.json` | Company profile and the 10-K / 8-K rows of the recent filing history, with 8-K item codes |
| `company_tickers.json` | Ticker-map rows of the sample entities, and those whose title starts with "GE" or "General Electric" |
| `fts_<hash>.json` | One EDGAR full-text search (catalog `sec_fulltext`) the source scout runs, as the search service returns it but kept to `hits.total` and, per hit, `_id` and `_source.{ciks, display_names, file_date, form, items}` (what `zetaris/sql/40_fulltext_total.sql` / `41_fulltext_hits.sql` read). `<hash>` is the hash in the live Zetaris table name `fts_<hash>_raw`, computed from the search URL, so sample mode finds the file for exactly the search live mode would run. Today: the scout's registration search for 3M's disposal filed 2024-04-04 (`"3m"` in Form 10-12B/10-12G filed 2022-10-12..2024-04-04; 51 documents) |
| `PROVENANCE.json` | For each file: source URL, fetch time (UTC), SHA-256 of the raw response and of the trimmed file, and the trim rule |

The trim is fixed and not case-specific: the same concepts for every entity, chosen before any run. The full-text searches cached are the ones `FULLTEXT` in `scripts/fetch_sample.py` lists by source entity and disposal date; the script computes each search (phrase, forms, window) with the source scout's own functions and checks the disposal is an 8-K item 2.01 filing in the source's submissions.

## Regenerate

```bash
# needs SEC_USER_AGENT="Your Name you@example.com" in the environment or .env (SEC fair-access policy)
uv run --project services/agent python scripts/fetch_sample.py
uv run --project services/agent python scripts/fetch_sample.py --only mmm,solv   # refresh some entities only
```

`--only` refreshes those entities' files (and their full-text searches) and the ticker map, and keeps the other files and their `PROVENANCE.json` records as they are (the 3M/Solventum files were added this way on 2026-10-24, so the GE files are still the 2026-10-23 fetch). `--out DIR` writes elsewhere (to compare with the committed slice); `--from DIR` trims raw responses already on disk instead of fetching. SEC data changes when companies file, so a later fetch can differ from the committed slice; `PROVENANCE.json` records which fetch it came from.
