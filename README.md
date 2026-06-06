# composite-domain-rating

> Consensus domain ranking aggregated from independent public sources using
> rank aggregation in the Borda family.

[![CI](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml/badge.svg)](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml)
[![dbt](https://img.shields.io/badge/dbt-1.8+-orange.svg)](https://www.getdbt.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status**: beta. The current project version is `v0.2.0-beta`; the current
scoring methodology is `v0.3.0-beta`. The ranking methodology uses Tranco,
Majestic Million, Cloudflare Radar, CrUX, and OpenPageRank. URLhaus,
ThreatFox, and PhishTank are integrated as a separate risk-observation layer
that does not affect `consensus_score`.

## Overview

`composite-domain-rating` is a data engineering project for building a
transparent consensus signal from several public domain-ranking sources.

The core idea is simple: no single ranking captures domain prominence well on
its own, but agreement across independent signals is more useful. The project
normalizes each source into percentile ranks, combines available signals with
equal weighting, and keeps source coverage explicit so sparse data is not
presented as false certainty.

In the current beta methodology, a domain receives a `consensus_score` when it is
present in at least three ranking sources. Domains with fewer signals remain in
the mart for coverage analysis, but their score is `NULL` and their
`coverage_tier` is `sparse`. Five-source rows use `coverage_tier = full`.

The implementation uses dbt Core on BigQuery, with ingestion scripts, source
lineage, CI checks, and public derived CSV archives.

## Quick links

- [Project specification](./SPEC.md)
- [Methodology](./METHODOLOGY.md)
- [Data license notes](./LICENSE-DATA.md)
- [Roadmap](./ROADMAP.md)
- [Analysis reports](./docs/analysis/README.md)
- [Data releases](https://github.com/ivan-aleshin/composite-domain-rating/releases)
- [Weekly refresh workflow](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/weekly_refresh.yml)

## Current Scope

The current beta scope includes Tranco, Majestic Million, Cloudflare Radar,
CrUX, OpenPageRank, and a separate public-threat-observation layer:

- download and normalize Tranco, Majestic, Cloudflare Radar, and OpenPageRank domains
- read CrUX monthly popularity buckets from the public BigQuery dataset
- download and normalize URLhaus, ThreatFox, and PhishTank observations
- load normalized raw data into BigQuery
- track source update status in `meta.source_update_log`
- build staging, intermediate, and mart dbt models
- validate source direction, coverage semantics, score range, risk-flag thresholding, and basic data quality
- publish only derived output, not raw third-party rankings

## Beta Data

Historical beta data snapshots are published as GitHub prereleases with tags
like `data-YYYY-WNN`.

The latest derived archive is also published under stable asset URLs:

- `https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-latest/domain_consensus_latest.csv.gz`
- `https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-latest/meta_latest.json`

The `data-latest` release is mutable and intended for automated consumers that
want the newest available snapshot. For reproducible historical analysis, use
the immutable weekly `data-YYYY-WNN` releases instead.

Each data release includes:

- `domain_consensus_<snapshot_date>.csv.gz` — derived public output
- `meta_<snapshot_date>.json` — lineage metadata with source statuses and
  methodology version

The public CSV is sorted by `consensus_score` descending, then `sources_count`
descending, then `registered_domain` ascending for deterministic ties.

The first beta code release was `v0.1.0-beta`. The current project version is
`v0.2.0-beta`; the current scoring methodology is `v0.3.0-beta`.

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

## Local source refresh

After configuring local BigQuery credentials, implemented ranking sources and
risk observations can be refreshed end to end:

```bash
# From the repository root
export SNAPSHOT_DATE=YYYY-MM-DD
export GCP_PROJECT_ID=YOUR_GCP_PROJECT_ID
export CLOUDFLARE_API_TOKEN=...  # or load it from your local .env
export THREATFOX_AUTH_KEY=...     # required for ThreatFox risk observations
export PHISHTANK_APP_KEY=...      # optional, improves PhishTank API access

python scripts/download_sources.py \
  --all \
  --date "$SNAPSHOT_DATE" \
  --output-dir data/raw

for source in tranco majestic cloudflare opr; do
  python scripts/load_to_bigquery.py \
    --source "$source" \
    --date "$SNAPSHOT_DATE" \
    --input-dir data/raw \
    --project "$GCP_PROJECT_ID" \
    --location US
done

for source in urlhaus threatfox phishtank; do
  python scripts/download_sources.py \
    --source "$source" \
    --date "$SNAPSHOT_DATE" \
    --output-dir data/raw

  python scripts/load_to_bigquery.py \
    --source "$source" \
    --date "$SNAPSHOT_DATE" \
    --input-dir data/raw \
    --project "$GCP_PROJECT_ID" \
    --location US
done

cd dbt
dbt run \
  --select +mart_domain_consensus_score \
  --vars "{\"snapshot_date\": \"$SNAPSHOT_DATE\"}"

dbt test --select \
  mart_domain_consensus_score \
  stg_crux__origins \
  stg_opr__domains \
  score_direction \
  sources_count_matches_percentiles \
  score_in_range \
  coverage_tier_consistency \
  source_percentiles_populated \
  security_flags_require_multiple_risk_sources \
  single_snapshot_date_per_mart
```

Raw source files under `data/raw/` are local-only and ignored by git. Production
fallback for stale sources is based on private BigQuery raw tables and
`meta.source_update_log`, not local cache files.

`CLOUDFLARE_API_TOKEN` is required for Cloudflare Radar downloads.
OpenPageRank uses DomCop's public zipped top 10M CSV and does not require an API
key for the current beta ingestion path.

`THREATFOX_AUTH_KEY` is required for ThreatFox bulk exports. `PHISHTANK_APP_KEY`
is optional, but recommended for automated PhishTank downloads.

Cloudflare Radar and CrUX are modeled as ranking buckets rather than exact
ranks. The pipeline uses the smallest bucket containing a domain as the source
signal.

OpenPageRank is downloaded from DomCop's zipped top 10M CSV. The file can
contain subdomains, so ingestion normalizes each row to `registered_domain` and
the OPR staging model collapses duplicate registered domains before scoring.

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
from `meta.source_update_log`, methodology version, row count, and the target
data release tag (`data-YYYY-WNN`). Local archive files under `data/archive/`
are ignored by git.

The mart keeps the full diagnostic source universe, including one-source-only
rows. Public archives exclude one-source-only rows and include domains that are
scored or observed by at least two ranking sources.

Diagnostic dbt analyses under `dbt/analyses/` help review source coverage,
overlap, percentile correlation, source agreement, and jackknife influence
before adding new ranking sources.

Public release diagnostics under `docs/analysis/` summarize published archive
shape, source combinations, risk-surface, schema changes, and week-over-week
stability using only GitHub release assets.

The same export path is used by the weekly GitHub Actions refresh workflow,
which can also be run manually from the Actions tab. Scheduled data releases
are created as GitHub prereleases so they do not replace code releases.

The workflow expects these repository secrets:

- `GCP_SA_KEY` — service account JSON for BigQuery jobs
- `CLOUDFLARE_API_TOKEN` — Cloudflare Radar API token
- `THREATFOX_AUTH_KEY` — ThreatFox export auth key
- `PHISHTANK_APP_KEY` — optional PhishTank app key for automated risk-layer downloads

## Disclaimer

Project code is released under the MIT License. The derived data archives are
built from third-party sources with varying terms, including non-commercial
terms. Review [LICENSE-DATA.md](./LICENSE-DATA.md) before using the archives in
commercial or production workflows.

This project is not affiliated with or endorsed by any data source providers.
Scores and flags are derived analytical signals, not authoritative security,
legal, financial, or business advice. Validate the output against your own
requirements before using it in production workflows.
