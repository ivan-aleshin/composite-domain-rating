# composite-domain-rating

> Consensus domain ranking aggregated from independent public sources using
> rank aggregation in the Borda family.

[![CI](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml/badge.svg)](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml)
[![dbt](https://img.shields.io/badge/dbt-1.8+-orange.svg)](https://www.getdbt.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status**: beta. The first three-source data release has been published with
Tranco, Majestic Million, and Cloudflare Radar, including BigQuery loading, dbt
models, data quality tests, lineage metadata, and weekly archive automation.

## Overview

`composite-domain-rating` is a data engineering project for building a
transparent consensus signal from several public domain-ranking sources.

The core idea is simple: no single ranking captures domain prominence well on
its own, but agreement across independent signals is more useful. The project
normalizes each source into percentile ranks, combines available signals with
equal weighting, and keeps source coverage explicit so sparse data is not
presented as false certainty.

For the current three-source beta slice, a domain receives a `consensus_score`
only when it is present in all three implemented ranking sources. Domains with
fewer signals remain in the mart for coverage analysis, but their score is
`NULL` and their `coverage_tier` is `sparse`. Scored beta rows currently use
`coverage_tier = partial`, matching the v1.0 tier taxonomy where `full` is
reserved for five-source coverage.

The implementation uses dbt Core on BigQuery, with ingestion scripts, source
lineage, CI checks, and public derived CSV archives.

## Quick links

- [Project specification](./SPEC.md)
- [Methodology](./METHODOLOGY.md)
- [Data license notes](./LICENSE-DATA.md)
- [Roadmap](./ROADMAP.md)
- [Data releases](https://github.com/ivan-aleshin/composite-domain-rating/releases)
- [Weekly refresh workflow](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/weekly_refresh.yml)

## Current Scope

The current working slice includes Tranco, Majestic Million, and Cloudflare
Radar:

- download and normalize Tranco, Majestic, and Cloudflare Radar domains
- load normalized raw data into BigQuery
- track source update status in `meta.source_update_log`
- build the first staging, intermediate, and mart dbt models
- validate source direction, coverage semantics, score range, and basic data quality
- publish only derived output, not raw third-party rankings

## Beta Data

Historical beta data snapshots are published as GitHub prereleases with tags
like `data-YYYY-WNN`.

Each data release includes:

- `domain_consensus_<snapshot_date>.csv.gz` — derived public output
- `meta_<snapshot_date>.json` — lineage metadata with source statuses and
  methodology version

The first beta code/methodology release is `v0.1.0-beta`.

For interpretation details and limitations, see [METHODOLOGY.md](./METHODOLOGY.md).
For source terms and publication constraints, see [LICENSE-DATA.md](./LICENSE-DATA.md).

## Getting started

```bash
# Clone
git clone https://github.com/ivan-aleshin/composite-domain-rating.git
cd composite-domain-rating

# Python environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# dbt setup (assumes BigQuery sandbox already configured)
cd dbt
mkdir -p ~/.dbt && cp profiles.yml.example ~/.dbt/profiles.yml  # then edit with your project_id
dbt deps
dbt debug
```

## Ranking-source walking skeleton

After configuring local BigQuery credentials, implemented ranking sources can be
run end to end:

```bash
# From the repository root
python scripts/download_sources.py --source tranco --date YYYY-MM-DD --output-dir data/raw
python scripts/download_sources.py --source majestic --date YYYY-MM-DD --output-dir data/raw
export CLOUDFLARE_API_TOKEN=...  # or load it from your local .env
python scripts/download_sources.py --source cloudflare --date YYYY-MM-DD --output-dir data/raw

python scripts/load_to_bigquery.py \
  --source tranco \
  --date YYYY-MM-DD \
  --input-dir data/raw \
  --project YOUR_GCP_PROJECT_ID \
  --location US

python scripts/load_to_bigquery.py \
  --source majestic \
  --date YYYY-MM-DD \
  --input-dir data/raw \
  --project YOUR_GCP_PROJECT_ID \
  --location US

python scripts/load_to_bigquery.py \
  --source cloudflare \
  --date YYYY-MM-DD \
  --input-dir data/raw \
  --project YOUR_GCP_PROJECT_ID \
  --location US

cd dbt
dbt run --select stg_tranco__domains stg_majestic__domains stg_cloudflare__domains mart_domain_consensus_score
dbt test --select stg_tranco__domains stg_majestic__domains stg_cloudflare__domains mart_domain_consensus_score score_direction
```

Raw source files under `data/raw/` are local-only and ignored by git. Production
fallback for stale sources is based on private BigQuery raw tables and
`meta.source_update_log`, not local cache files.

Cloudflare Radar is modeled as ranking buckets rather than exact ranks. The
pipeline uses the smallest bucket containing a domain as the source signal.

## Derived archive export

After a successful dbt run, the public mart can be exported as release-ready
artifacts:

```bash
python scripts/archive_to_release.py \
  --project YOUR_GCP_PROJECT_ID \
  --marts-dataset marts \
  --meta-dataset meta \
  --output-dir data/archive
```

The script writes:

- `domain_consensus_<snapshot_date>.csv.gz`
- `meta_<snapshot_date>.json`

The CSV contains only derived public columns, not raw third-party ranks or
source-specific percentile columns. The metadata JSON records source statuses
from `meta.source_update_log`, methodology version, row count, and the planned
data release tag (`data-YYYY-WNN`). Local archive files under `data/archive/`
are ignored by git.

Diagnostic dbt analyses under `dbt/analyses/` help review source coverage,
overlap, percentile correlation, source agreement, and jackknife influence
before adding new ranking sources.

The same export path is used by the weekly GitHub Actions refresh workflow,
which can also be run manually from the Actions tab. Scheduled data releases
are created as GitHub prereleases so they do not replace code releases.

The workflow expects these repository secrets:

- `GCP_SA_KEY` — service account JSON for BigQuery jobs
- `CLOUDFLARE_API_TOKEN` — Cloudflare Radar API token

## Disclaimer

Project code is released under the MIT License. The derived data archives are
built from third-party sources with varying terms, including non-commercial
terms. Review [LICENSE-DATA.md](./LICENSE-DATA.md) before using the archives in
commercial or production workflows.

This project is not affiliated with or endorsed by any data source providers.
Scores and flags are derived analytical signals, not authoritative security,
legal, financial, or business advice. Validate the output against your own
requirements before using it in production workflows.
