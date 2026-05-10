# composite-domain-rating — Project Specification

> **Document scope**: high-level product specification — what the project is and
> why. Near-term delivery milestones live in [`ROADMAP.md`](./ROADMAP.md).

---

## Overview

**Repository:** `composite-domain-rating`

**One-liner:**
A dbt + BigQuery project that aggregates multiple independent public domain
rankings into a transparent consensus score using rank aggregation in the
Borda family — the same family Tranco itself uses internally.

**Positioning:** Open-source data engineering project. Demonstrates
production-style data modeling on a well-defined problem within real
infrastructure constraints (BigQuery sandbox, free tier), while producing
derived outputs that can be reused subject to the project license and source
data terms.

---

## Problem Statement

There is no single authoritative measure of "domain quality" or "prominence".
Different public sources measure different dimensions:

- **Tranco** — traffic / popularity (aggregated from multiple traffic signals)
- **Majestic Million** — link graph breadth (referring subnets)
- **OpenPageRank** — position in the web graph
- **Cloudflare Radar** — DNS visibility (independent DNS signal)
- **CrUX** — real Chrome users (real-user presence signal)

Each source has different coverage, scale, and update frequency. None alone
is a reliable single ranking — but their consensus is more informative than
any individual source.

**Analogy:** Olympic combined events. An athlete doesn't need to win every
discipline — the winner is the best performer across all disciplines combined.

---

## Methodology

### Approach: Rank Aggregation in the Borda Family

The project follows the same family of methods Tranco uses internally:
**equal-weight rank aggregation** without arbitrary differential weights.

Each source is normalized to a 0–1 percentile rank. The final consensus score
is the average of available percentile ranks, scaled to 0–100.

```sql
-- Inverted PERCENT_RANK so that "best" gets ~1.0
1 - PERCENT_RANK() OVER (ORDER BY tranco_rank ASC)        AS p_tranco
1 - PERCENT_RANK() OVER (ORDER BY ref_subnets DESC)       AS p_majestic
1 - PERCENT_RANK() OVER (ORDER BY open_page_rank DESC)    AS p_opr
1 - PERCENT_RANK() OVER (ORDER BY radar_rank_bucket ASC)  AS p_radar
1 - PERCENT_RANK() OVER (ORDER BY crux_rank_bucket ASC)   AS p_crux
```

```
consensus_score = AVG(available percentile ranks) × 100
```

Result: 0–100 score. **Higher = more prominent across all sources.**

### Why Equal Weighting

Differential weighting (e.g., "Authority 35% + Popularity 25%") requires
empirical ground truth that doesn't exist for "domain quality" as a concept.
Equal weighting is the principled default. The Tranco paper (Le Pochat et al.,
NDSS 2019) demonstrates this is sufficient for stable, manipulation-resistant
rankings.

### Coverage Threshold

A consensus score requires at least **3 of 5 sources** by definition — fewer
signals are insufficient for "consensus". Domains with sparse coverage are
reported with `consensus_score = NULL` and `coverage_tier = 'sparse'`,
preserving raw signals without misleading aggregation.

| Coverage tier | Sources present | Score |
|---|---|---|
| `full` | 5/5 | computed |
| `high` | 4/5 | computed |
| `partial` | 3/5 | computed |
| `sparse` | 1–2/5 | NULL |

### Methodology Versioning

The mart and public CSV include a `methodology_version` column. When the
formula changes (e.g., source addition, weighting change), the version is
bumped — enabling rigorous historical comparisons across versions.

### Sensitivity Analysis

The methodology is validated through:

- **Pairwise correlations** between source percentile ranks (identifies
  redundant sources)
- **Jackknife stability** (correlation of full score vs. score-without-source-X
  for each source)
- **Top-N week-over-week stability** (deferred to v1.1, requires accumulated
  history)

Results are published in `METHODOLOGY.md`.

### Risk Layer (Separate from Consensus Score)

Security signals are **not mixed into the consensus score**. Mixing popularity
and security would produce a misleading single number. They are a separate
attribute layer:

- `security_flags_observed` — boolean, true if observed in ≥ 2 risk feeds
- `risk_sources_count` — number of risk feeds that observed the domain
- `threat_types` — array of observed threat types
- `last_threat_seen` — most recent observation date

A domain can have a high consensus score and security flags — the data shows
both facts without hiding either. The framing is intentionally cautious:
"observed in public threat intelligence" is not the same as an authoritative
security verdict.

---

## Data Sources (v1.0)

### Ranking Sources

| Source | Signal | Format | Status |
|---|---|---|---|
| Tranco | Traffic / popularity (Borda aggregate) | CSV bulk (30-day list) | Required |
| Majestic Million | Link graph breadth (RefSubNets) | CSV bulk | Required |
| Cloudflare Radar | DNS visibility (popularity bucket) | API | Required |
| CrUX | Real Chrome users (rank bucket) | BigQuery public dataset | Required |
| OpenPageRank | Web graph position | CSV bulk | **Conditional** — drops to v1.1 if licensing or access blocks > 2h |

CrUX and Cloudflare Radar contribute bucket-based signals — domains within the
same bucket are tied. This is documented as a known limitation of those
sources, not a flaw in aggregation.

### Risk / Reputation Sources

| Source | Signal | License |
|---|---|---|
| URLhaus (abuse.ch) | Malware-hosting URLs | CC0 |
| ThreatFox (abuse.ch) | Broad IOC export | CC0 |
| PhishTank | Phishing URLs | Free with attribution |

### Reference Data

| Source | Usage |
|---|---|
| Public Suffix List (via tldextract) | Domain normalization |
| Curated TLD categories seed | TLD quality enrichment in mart |

### Excluded / Deferred Sources

| Source | Reason |
|---|---|
| Cisco Umbrella | Already inside Tranco (avoid double-counting) |
| RDAP / WHOIS | Rate limits, privacy redaction |
| Spamhaus DBL, SURBL | Public DNSBL not permitted at 1M scale |
| Google Safe Browsing | ToS restrictions for bulk use |
| crt.sh, HTTP Archive, HSTS Preload, Common Crawl, Wikidata | Deferred to v1.1+ |

---

## Public Output

A weekly aggregated CSV is published to GitHub Releases. Schema:

```
registered_domain
consensus_score              -- 0–100, or NULL if coverage < threshold
coverage_tier                -- full | high | partial | sparse
sources_count                -- 0–5
ranking_sources_present      -- comma-separated list
tld_category                 -- from curated seed
is_spam_prone_tld            -- from curated seed
security_flags_observed      -- boolean (≥ 2 risk sources)
risk_sources_count           -- 0–3
threat_types                 -- array
last_threat_seen             -- date or NULL
snapshot_date                -- ISO date
methodology_version          -- e.g., 'v1.0.0'
```

**Important:** the public CSV contains **only derived output** — no raw
ranks from individual sources are redistributed. This respects source
licensing terms (transformative use) while remaining genuinely useful.

A lineage `meta_<date>.json` accompanies each release with per-source status,
snapshot dates, row counts, and methodology version.

---

## Infrastructure

### Stack

- **Warehouse**: BigQuery sandbox (free tier — 10 GB storage, 1 TB queries/month)
- **Transformation**: dbt Core (BigQuery adapter)
- **Orchestration**: GitHub Actions (weekly cron)
- **Documentation**: dbt docs auto-deployed to GitHub Pages
- **History**: GitHub Releases as compressed CSV archive
- **Citation**: Zenodo DOI integration

### Why BigQuery Sandbox

Free, permanent, no billing required. Sandbox imposes a 60-day partition
expiration — addressed by archiving aggregated CSV snapshots to GitHub
Releases on every weekly run. This is documented as a deliberate design
choice; production deployment would migrate to billed BigQuery.

### Resilience

The pipeline is designed to survive partial source failures:

- Each source is independently downloaded; one failure does not block others
- Status tracking per source: `fresh` / `stale` / `missing`
- Stale data (≤ 14 days) used as fallback when fresh download fails
- Missing source → empty raw table; downstream models gracefully degrade
- Source statuses surfaced in lineage JSON of each data release
- Tiered alerting: 1 missing source = warning, ≥ 2 = pipeline failure

---

## What This Project Demonstrates

For a portfolio context, the project demonstrates:

- **Methodologically defensible aggregation** — Tranco-family rank aggregation,
  not arbitrary weighted scoring
- **Honest handling of sparse data** — explicit coverage tiers, no imputation
- **Heterogeneous source integration** — different formats, scales, update
  frequencies, licensing terms
- **Production-style dbt** — full layer separation, tests, snapshots,
  source freshness, dbt-expectations
- **CI/CD discipline** — automated linting, weekly refresh, automated docs deploy
- **Resilience patterns** — graceful degradation, staleness tracking, tiered alerting
- **Cost discipline within free-tier constraints** — `maximum_bytes_billed`,
  partition pruning, staging-as-view for public datasets
- **Reproducibility** — methodology versioning, lineage JSON, file-hash
  verified ingestion
- **Honest output framing** — `security_flags_observed` (not `is_malware`),
  explicit interpretation guidance in README

---

## References

- Le Pochat, V. et al. (2019). *Tranco: A Research-Oriented Top Sites Ranking
  Hardened Against Manipulation*. NDSS 2019. — Foundational reference for
  rank aggregation methodology used here.
- dbt Labs. *How we structure our dbt projects*. — Layer separation pattern.

---

## Disclaimer

Open-source project released under the MIT License. Commercial use is welcome,
subject to the license terms and the terms of the underlying data sources.

This project is not affiliated with or endorsed by any data source providers.
The consensus score reflects observed prominence across public sources, not an
editorial judgment of domain quality. Security flags are derived analytical
signals, not authoritative security verdicts. Validate the output against your
own requirements before using it in production workflows.
