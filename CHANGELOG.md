# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-beta] - 2026-05-16

### Added
- Initial repository scaffold (Sprint 0)
- Public roadmap.
- Tranco downloader, BigQuery raw loader, initial dbt mart, and score-direction test.
- Majestic Million downloader, BigQuery raw loader, staging/intermediate models, and mart integration.
- Source registry refactor for ingestion/load scripts and a mart `sources_count` invariant test.
- Cloudflare Radar bucket downloader, BigQuery raw loader config, staging/intermediate models, and mart integration.
- Beta scoring semantics requiring the configured minimum source coverage before emitting `consensus_score`.
- Mart validation tests for score range, coverage consistency, source population, and percentile population.
- Run-level mart `snapshot_date` semantics for coherent weekly archive snapshots.
- Release archive exporter for derived CSV and lineage JSON artifacts.
- Mart validation test requiring a single archive snapshot date per build.
- Weekly refresh workflow for source ingestion, dbt build/test, archive export, and data prerelease publishing.
