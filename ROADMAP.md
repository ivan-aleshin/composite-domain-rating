# Roadmap

This project is being built in small, reviewable slices. The priority is to
keep the public repository honest: each milestone should describe working
behavior, not just planned architecture.

## 1. Project Scaffold

- dbt project structure for BigQuery
- dependency and lint configuration
- CI workflow for dbt parsing and SQL linting
- public specification and roadmap

## 2. First End-to-End Source

- ingest Tranco ranking data
- normalize domains to registered domains
- load raw data into BigQuery
- add staging and intermediate dbt models
- produce an initial consensus mart from one source
- add tests for score direction and basic uniqueness/null constraints

## 3. Multi-Source Ranking Core

- add Majestic Million, Cloudflare Radar, and CrUX
- evaluate the effect of CrUX on coverage and source agreement
- add OpenPageRank as the planned fifth ranking source once API access is
  configured
- normalize source-specific ranks and buckets into comparable percentile scores
- compute equal-weight consensus scores for domains with sufficient coverage
- expose coverage tiers so sparse domains are not over-interpreted

## 4. Lineage, Resilience, and Risk Signals

- track source freshness, row counts, hashes, and snapshot dates
- allow graceful degradation when one source is stale or missing
- add separate public threat-intelligence flags without mixing them into the
  consensus score
- document source licensing and attribution constraints

## 5. Public Output and Documentation

- publish derived CSV snapshots with lineage metadata
- generate dbt documentation
- write methodology notes with validation and limitations
- add data license notes and release notes
- citation metadata remains optional until there is a stable citation need
