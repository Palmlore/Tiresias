# Data

`sample/` holds the offline slice used by `SAMPLE_MODE=true`: trimmed SEC responses and the Zetaris result rows for the showcase entities. Each file records source URL, fetch time (UTC) and SHA-256. Sources, licences and entities are in [docs/data-sources.md](../docs/data-sources.md). Planned: `scripts/fetch_sample.py` regenerates the slice using `SEC_USER_AGENT`.
