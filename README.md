# composite-domain-rating

> Consensus domain ranking aggregated from independent public sources using
> rank aggregation in the Borda family.

[![CI](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml/badge.svg)](https://github.com/ivan-aleshin/composite-domain-rating/actions/workflows/ci.yml)
[![dbt](https://img.shields.io/badge/dbt-1.8+-orange.svg)](https://www.getdbt.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Status**: early implementation scaffold. The methodology and project shape are
documented; the ingestion pipeline and dbt models are being built incrementally.

## Overview

`composite-domain-rating` is a data engineering project for building a
transparent consensus signal from several public domain-ranking sources.

The core idea is simple: no single ranking captures domain prominence well on
its own, but agreement across independent signals is more useful. The project
normalizes each source into percentile ranks, combines available signals with
equal weighting, and keeps source coverage explicit so sparse data is not
presented as false certainty.

The implementation uses dbt Core on BigQuery, with ingestion scripts, source
lineage, CI checks, and a public derived CSV planned as the project matures.

## Quick links

- [Project specification](./SPEC.md)
- [Roadmap](./ROADMAP.md)

## Current Scope

The first working version focuses on a narrow vertical slice:

- ingest one ranking source end to end
- normalize registered domains consistently
- load raw data into BigQuery
- add the first dbt staging, intermediate, and mart models
- validate score direction and basic data quality
- publish only derived output, not raw third-party rankings

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

## Disclaimer

Open-source project released under the MIT License. Commercial use is welcome,
subject to the license terms and the terms of the underlying data sources.

This project is not affiliated with or endorsed by any data source providers.
Scores and flags are derived analytical signals, not authoritative security,
legal, financial, or business advice. Validate the output against your own
requirements before using it in production workflows.
