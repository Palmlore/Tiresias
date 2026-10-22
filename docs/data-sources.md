# Data sources

All data is public. No personal or confidential data is used.

| Source | Endpoint template | Used for | Licence / terms | Access |
|---|---|---|---|---|
| SEC EDGAR XBRL company facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json` | Reported values by concept, period and filing (detects restatements) | US government public data; SEC fair-access policy | No key. `User-Agent` must include a contact email. ≤10 requests/s. |
| SEC EDGAR XBRL company concept | `https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/us-gaap/{Concept}.json` | One concept per company (the Zetaris recipe pattern) | same | same |
| SEC EDGAR submissions | `https://data.sec.gov/submissions/CIK{cik10}.json` | Company profile, SIC, former names, filing history with 8-K item codes (e.g. 2.01 completion of a disposal) | same | same |
| SEC ticker map | `https://www.sec.gov/files/company_tickers.json` | Ticker → CIK → name (entity resolution in Loop B) | same | same |
| company_dns (mediumroast.io) | `https://company-dns.mediumroast.io/V4.0/na/sic/code/%25` | SIC hierarchy (industry context) | Apache-2.0 (from the SEC SIC list) | No key. `User-Agent` required. Rate limited. |

The **bounded source catalog** the agent may draw on is [`catalog/sources.yaml`](../catalog/sources.yaml). The agent cannot reach anything outside it.

## Offline sample (`data/sample/`)

The sample is a trimmed slice of the responses above for the showcase entities (GE, GE HealthCare, GE Vernova) and only the concepts the cases use. Each file records its source URL, fetch timestamp and SHA-256. `scripts/fetch_sample.py` regenerates it (planned). The sample is **fetched during the build window**; nothing was prepared beforehand.

## Entities (checked on 2026-10-22)

| Entity | Ticker | CIK |
|---|---|---|
| General Electric (GE Aerospace) | GE | 0000040545 |
| GE HealthCare Technologies | GEHC | 0001932393 |
| GE Vernova | GEV | 0001996810 |
| Fallback: 3M / Solventum | MMM / SOLV | 0000066740 / 0001964738 |

## Redistribution

The Zetaris recipes note that EDGAR's licence is ambiguous, because filings are written by the filers, and advise against bulk redistribution of extracted data. The committed offline sample is therefore limited to the few concepts and entities the cases need, with source URLs. It is not a bulk copy.

## Known data caveats

- `companyfacts` has **no segment dimensions**, so "which business line" can't be answered from it alone.
- The same concept can appear under different names over time (`Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`). The analyst must handle this mapping.
- On 2026-10-22 the `companyconcept` endpoint returned empty `units` for GE HealthCare's revenue concept while `companyfacts` held 41 values. `companyfacts` is the primary path.
- Restated values appear as several values for the same (concept, period), told apart by accession number and filing date.
