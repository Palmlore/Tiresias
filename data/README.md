# Data

`sample/` holds the offline slice used by `SAMPLE_MODE=true`: trimmed SEC EDGAR responses for the showcase entities (GE, GE HealthCare, GE Vernova). Sources, licences and entities are in [docs/data-sources.md](../docs/data-sources.md).

| File | Content |
|---|---|
| `<entity>_companyfacts.json` | XBRL company facts, same structure as the SEC response, kept to 7 core income-statement concepts (all restated values kept) |
| `<entity>_submissions.json` | Company profile and the 10-K / 8-K rows of the recent filing history, with 8-K item codes |
| `company_tickers.json` | Ticker-map rows whose title starts with "GE" or "General Electric" |
| `PROVENANCE.json` | For each file: source URL, fetch time (UTC), SHA-256 of the raw response and of the trimmed file, and the trim rule |

The trim is fixed and not case-specific: the same concepts for every entity, chosen before any run.

## Regenerate

```bash
# needs SEC_USER_AGENT="Your Name you@example.com" in the environment or .env (SEC fair-access policy)
uv run --project services/agent python scripts/fetch_sample.py
```

`--out DIR` writes elsewhere (to compare with the committed slice); `--from DIR` trims raw responses already on disk instead of fetching. SEC data changes when companies file, so a later fetch can differ from the committed slice; `PROVENANCE.json` records which fetch it came from.
