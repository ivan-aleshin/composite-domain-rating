# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Risk aggregation model and mart risk-field integration for URLhaus
  observations without changing consensus scoring.
- Weekly refresh steps for URLhaus risk-source download/load, separate from the
  ranking-source health gate.
- URLhaus downloader, BigQuery load configuration, raw source metadata, and
  staging model as the first risk-layer source on the risk integration branch.
- OpenPageRank bulk CSV ingestion, BigQuery loading, staging/intermediate
  models, mart integration, and tests as the fifth ranking source.
- Stable `data-latest` release asset support for automated consumers that need
  a persistent URL for the newest derived archive.
- CrUX public BigQuery schema smoke check for the weekly refresh workflow.
- Beta methodology documentation.
- Data license notes for current ranking sources and derived archive output.
- Source diagnostic dbt analyses for coverage, overlap, correlation, sparse-row breakdown,
  source agreement, disagreement outliers, and jackknife influence.
- OpenPageRank coverage, overlap, correlation, sparse-row breakdown, agreement,
  disagreement outlier, and jackknife diagnostic analyses.
- Five-source diagnostic methodology summary based on the `2026-05-18`
  development mart.
- CrUX public BigQuery source integration with staging, percentile scoring,
  mart integration, tests, diagnostics, and documentation updates.
- Public archive inclusion diagnostics and export filtering to exclude
  one-source-only rows while keeping them in the internal mart.
- Methodology version corrected to `v0.2.0-beta` for the CrUX scoring change;
  previous beta archives used the premature default `v1.0.0` label.
- Methodology version bumped to `v0.3.0-beta` for OpenPageRank scoring.

### Changed
- Simplify README local source refresh instructions around `--all`, a load
  loop, and the current five-source beta workflow.
- Sort public CSV archives by consensus score, source coverage, and domain for
  deterministic ranking output.
- Align the weekly refresh source-health gate with graceful degradation policy:
  one missing or empty loaded source warns, while two or more fail the run.
- Increase stale fallback TTLs for ranking sources and set future safety/risk
  feed TTLs to 15 days.
- Made GitHub data release publication rerun-friendly by updating existing
  prereleases and replacing assets when a data tag already exists.
- Synchronized source-scope documentation around OpenPageRank as the fifth
  ranking source and documented out-of-scope alternatives.
- Clarified code/data licensing language for derived archives.

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
