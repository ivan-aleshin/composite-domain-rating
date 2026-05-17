# Methodology

This document describes the beta methodology used by `composite-domain-rating`.
It is written for readers who want to understand what the score means, what it
does not mean, and how the public archive should be interpreted.

## Scope

`composite-domain-rating` builds a derived consensus signal from independent
public ranking sources. The beta release currently uses four ranking sources:

| Source | Signal used | Direction |
|---|---|---|
| Tranco | Domain rank from the downloaded top-1M list | Lower rank is better |
| Majestic Million | `RefSubNets` | Higher value is better |
| Cloudflare Radar | Smallest observed ranking bucket | Smaller bucket is better |
| CrUX | Experimental popularity rank bucket | Smaller bucket is better |

The output is a weekly derived CSV archive. It is not a mirror of raw source
data, and it does not redistribute raw third-party ranks.

## Domain Identity

All sources are normalized to `registered_domain`.

Normalization includes:

- extracting the registered domain rather than keeping arbitrary subdomains;
- converting internationalized domains to ASCII/Punycode;
- dropping malformed domains, IP addresses, control characters, and reserved
  suffixes that should not be treated as public domains.

This gives the mart a stable join key across sources. It also means the project
is intentionally a domain-level signal, not a URL-level, hostname-level, or
origin-level signal.

## Source Scoring

Each source is converted to a percentile-like score between 0 and 1 where higher
is better.

The implementation uses inverted `PERCENT_RANK()`:

```sql
1 - PERCENT_RANK() OVER (ORDER BY source_signal <direction>)
```

Examples:

- Tranco rank: `ORDER BY tranco_rank ASC`
- Majestic RefSubNets: `ORDER BY ref_subnets DESC`
- Cloudflare Radar bucket: `ORDER BY rank_bucket ASC`
- CrUX popularity bucket: `ORDER BY crux_rank_bucket ASC`

`PERCENT_RANK()` includes `NULL` values in the window if they are present.
Therefore, intermediate models filter out null source signals before applying
the scoring macro. This is a correctness requirement: otherwise sparse source
coverage would shift percentiles for valid rows.

Ties receive the same percentile rank. This is expected and important for
bucketed sources such as Cloudflare Radar and CrUX.

## Consensus Score

The current beta methodology version is `v0.2.0-beta`.

The public `consensus_score` is an equal-weight average of available source
percentiles, scaled to 0-100:

```text
consensus_score = average(non-null source percentiles) * 100
```

In the beta release, `min_sources_for_score = 3`. A domain must appear in at
least three implemented beta ranking sources to receive a score. Domains with
fewer signals remain in the mart and public CSV, but their score is `NULL`.

The project uses equal weighting because there is no external ground truth for
"domain prominence" that would justify a learned or manually weighted formula.
Equal weighting is easier to explain, easier to audit, and avoids implying a
precision the data does not support.

## Coverage Tiers

Coverage tiers describe how many ranking sources support a score:

| Coverage tier | Sources present | Score |
|---|---:|---|
| `full` | 5 | computed |
| `high` | 4 | computed |
| `partial` | 3 | computed |
| `sparse` | 1-2 | `NULL` |

The beta release has four implemented ranking sources, so the strongest current
coverage tier is `high`. The `full` tier is reserved for future five-source
coverage.

Interpret `consensus_score` together with `coverage_tier` and
`ranking_sources_present`. A score based on partial coverage is useful, but it
is less robust than a future score backed by more independent sources.

## Public Archive

Weekly data releases contain:

- `domain_consensus_<snapshot_date>.csv.gz`
- `meta_<snapshot_date>.json`

The CSV contains only derived public columns:

- domain identity;
- consensus score and coverage fields;
- source-presence summary;
- placeholder/reference fields for future TLD and risk layers;
- snapshot and methodology version.

The CSV does not include raw third-party ranks, source-specific signal values,
or source percentile columns.

The internal mart keeps the full diagnostic source universe, including
one-source-only rows. Public archives exclude one-source-only rows and publish
domains that are scored or observed by at least two ranking sources. This keeps
the archive focused on domains with some multi-source evidence while preserving
the broader universe for coverage diagnostics.

The lineage JSON records source statuses, row counts, source metadata, the
methodology version, and the archive row count.

## Snapshot Dates

The mart `snapshot_date` is the index build date, not the date of every raw
source. This keeps each weekly archive internally coherent: one release is one
index snapshot.

Individual source dates and staleness information are recorded in the lineage
JSON. This distinction matters because sources can refresh at different times or
fall back to a recent stale BigQuery raw table if a fresh download fails.

## Resilience Model

The pipeline treats each source independently:

- successful fresh load: source status is `fresh`;
- failed fresh load with acceptable existing BigQuery raw table: status is
  `stale`;
- failed fresh load without acceptable fallback: status is `missing`, and an
  empty raw table is created with the expected schema.

BigQuery raw tables are the production fallback for the latest successful copy.
Local files under `data/raw/` are development artifacts only.

## Infrastructure Trade-Offs

The project is intentionally built on free-tier-friendly infrastructure:

- BigQuery sandbox for warehouse and dbt execution;
- GitHub Actions for orchestration;
- GitHub Releases for derived historical archives.

BigQuery sandbox is useful for a portfolio-scale project, but it is not a full
historical warehouse. The project therefore stores long-term derived history as
compressed CSV snapshots in GitHub Releases. This is a design trade-off, not an
attempt to hide infrastructure limitations.

Raw third-party source data is not published in GitHub Releases.

## Current Limitations

- The beta score uses four ranking sources.
- Cloudflare Radar and CrUX are bucketed, so many domains intentionally share
  the same source percentile.
- CrUX is an origin-level dataset that is collapsed to registered domains; this
  preserves domain-level identity but loses origin-level nuance.
- Majestic measures link graph breadth, not user traffic.
- Tranco is itself an aggregate ranking and is not independent of every ranking
  signal on the web.
- Scores are not security verdicts, business quality ratings, or financial
  recommendations.
- The public schema and methodology may evolve before `v1.0.0`.

## Deferred Work

Potential future additions:

- risk/reputation feeds as a separate attribute layer;
- sensitivity analysis across source percentiles;
- fuller public documentation for v1.0.

OpenPageRank remains a conditional future candidate rather than a v1.0 blocker.
Access, bulk availability, and license interpretation must be clear before it
can be included.

Source expansion should be guided by diagnostics in `dbt/analyses/`, especially
coverage distribution, pairwise overlap, sparse-row breakdown, and percentile
correlation between implemented sources. Source agreement and jackknife
influence analyses are useful for identifying domains whose score is strongly
boosted or suppressed by one source relative to the others.

## References

- Tranco methodology: https://tranco-list.eu/methodology
- Tranco paper: https://tranco-list.eu/assets/tranco-ndss19.pdf
- Majestic Million: https://majestic.com/reports/majestic-million
- Cloudflare Radar datasets API: https://developers.cloudflare.com/api/resources/radar/subresources/datasets/
- Cloudflare Radar licensing note: https://radar.cloudflare.com/about
- CrUX on BigQuery: https://developer.chrome.com/docs/crux/bigquery/
