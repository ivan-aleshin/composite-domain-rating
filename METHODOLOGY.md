# Methodology

This document describes the beta methodology used by `composite-domain-rating`.
It is written for readers who want to understand what the score means, what it
does not mean, and how the public archive should be interpreted.

## Scope

`composite-domain-rating` builds a derived consensus signal from independent
public ranking sources. The beta release currently uses five ranking sources:

| Source | Signal used | Direction |
|---|---|---|
| Tranco | Domain rank from the downloaded top-1M list | Lower rank is better |
| Majestic Million | `RefSubNets` | Higher value is better |
| Cloudflare Radar | Smallest observed ranking bucket | Smaller bucket is better |
| CrUX | Experimental popularity rank bucket | Smaller bucket is better |
| OpenPageRank | OpenPageRank decimal score | Higher value is better |

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

## Risk Signal Grain

Risk and reputation feeds are modeled as a separate observation layer. They do
not affect `consensus_score`.

Risk feeds often observe URLs, hosts, or subdomains rather than registered
domains. To match the ranking grain, these observations are aggregated to
`registered_domain`. A risk observation therefore means that a public threat
feed observed one or more URLs, hosts, or subdomains under that registered
domain.

It does not mean that the registered domain itself, all of its subdomains, or
the domain owner are malicious. Public output should use observation-oriented
language such as `security_flags_observed`, `risk_sources_count`,
`threat_types`, and `last_threat_seen`, and avoid verdict-style language such
as `is_malicious`.

The current risk-layer branch uses URLhaus malware URL observations, ThreatFox
domain/URL IOC observations, and PhishTank online phishing URL observations.
All three are collapsed to registered domains before they are joined to the
ranking mart.

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
- OpenPageRank decimal score: `ORDER BY openpagerank_decimal DESC`

`PERCENT_RANK()` includes `NULL` values in the window if they are present.
Therefore, intermediate models filter out null source signals before applying
the scoring macro. This is a correctness requirement: otherwise sparse source
coverage would shift percentiles for valid rows.

Ties receive the same percentile rank. This is expected and important for
bucketed sources such as Cloudflare Radar and CrUX.

## Consensus Score

The current beta methodology version is `v0.3.0-beta`.

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

Interpret `consensus_score` together with `coverage_tier` and
`ranking_sources_present`. A score based on partial coverage is useful, but it
is less robust than a score backed by more independent sources.

## Five-Source Diagnostics

A diagnostic run against the `2026-05-18` development mart produced 18.36M
internal domain rows under methodology `v0.3.0-beta`.

The scored universe contained 878,514 domains:

- 224,743 domains with all five ranking sources;
- 178,187 domains with four sources;
- 475,584 domains with three sources.

The broader public archive policy would include 3.34M rows: scored domains plus
2.46M sparse domains observed by at least two sources. Single-source rows remain
available in the internal mart for coverage diagnostics, but are excluded from
the public archive.

The strongest source relationship in this run was Tranco and Cloudflare Radar
(Spearman correlation around 0.58 on overlapping domains). OpenPageRank showed
weaker correlations with the existing sources, including CrUX, which supports
keeping it as a complementary fifth source rather than treating it as redundant.

Jackknife diagnostics showed that CrUX and Majestic have the largest average
score influence in the current equal-weight formula, while OpenPageRank is
meaningful but not dominant. Source-agreement diagnostics also show a substantial
low-agreement tail, even among high-coverage domains. This is expected for a
consensus score built from different source universes, and it is one reason the
project keeps source presence and coverage fields in the public output.

## Public Archive

Weekly data releases contain:

- `domain_consensus_<snapshot_date>.csv.gz`
- `meta_<snapshot_date>.json`

The CSV contains only derived public columns:

- domain identity;
- consensus score and coverage fields;
- source-presence summary;
- placeholder/reference fields for future TLD enrichment;
- risk observation fields where available;
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

- The beta score uses five ranking sources.
- Cloudflare Radar and CrUX are bucketed, so many domains intentionally share
  the same source percentile.
- CrUX is an origin-level dataset that is collapsed to registered domains; this
  preserves domain-level identity but loses origin-level nuance.
- Majestic measures link graph breadth, not user traffic.
- OpenPageRank measures web-graph centrality, not user traffic.
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

Some sources are intentionally out of scope. HTTP Archive and OpenINTEL are too
large for the project's free-tier BigQuery sandbox design. Wikidata official
website data is useful entity metadata, but it is not a ranking source. Direct
Common Crawl graph processing remains a fallback only because OpenPageRank
packages a broad web-graph signal with lower operational cost.

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
- OpenPageRank terms: https://www.domcop.com/openpagerank/terms-and-conditions
- OpenPageRank attribution: https://www.domcop.com/openpagerank/attribution
- URLhaus API and exports: https://urlhaus.abuse.ch/api/
- ThreatFox API and exports: https://threatfox.abuse.ch/api/
- PhishTank developer information: https://phishtank.org/developer_info.php
