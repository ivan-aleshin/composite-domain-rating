# Experimental Dynamic Domain Reputation Index

Status: experimental. Methodology version: `ddri-v0.1.1-experimental`.

The Dynamic Domain Reputation Index (DDRI) is a temporal layer over the public
weekly `consensus_score`. It is built only from published aggregate archives.
It does not retain or redistribute source-specific ranks or percentiles, and it
does not change the primary consensus dataset.

## Publication

The `Weekly Experimental DDRI` workflow starts after a successful `Weekly Data
Refresh`. It attaches two additional assets to the same immutable weekly
release:

- `domain_reputation_experimental_<snapshot_date>.csv.gz`
- `meta_reputation_experimental_<snapshot_date>.json`

Mutable copies are attached to `data-latest` as:

- `domain_reputation_experimental_latest.csv.gz`
- `meta_reputation_experimental_latest.json`

The workflow can also be run manually for `data-latest` or an immutable
`data-YYYY-WNN` tag. Its failure does not affect the primary consensus release.
Consensus workflow runs without an explicit date derive `snapshot_date` from
the latest UTC Sunday, so a delayed runner does not move the release into
another ISO week.

## Components

`reputation_score` is an exponentially weighted mean of the latest four weekly
consensus scores with a two-week half-life. At least three observations are
required.

`reputation_confidence` is the geometric mean of four values over eight weeks:
history completeness, average ranking-source coverage, source-set continuity,
and rank-band-normalized residual-noise confidence. It is reported in `[0, 1]`
and is an exploratory heuristic, not a probability.

`reputation_trend` is `rising`, `falling`, `stable`, or `uncertain`. It uses an
eight-week OLS slope after removing 1.5 slope standard errors and requiring a
rank-band-relative material movement. `trend_strength` is the signed confirmed
slope in consensus-score points per week, clipped to `[-3, 3]` before the
uncertainty threshold is removed.

`observed_risk` summarizes current and recent risk observations as
`multi-source-observed`, `single-source-observed`, `recent-history-only`, or
`none-observed`. `none-observed` is not evidence that a domain is safe.

## Candidate Score

The default candidate is the four-week EWMA. A change in the published
`ranking_sources_present` value is treated as a structural shock. On that week,
the candidate accepts half of the movement between the previous and current
EWMA levels:

```text
normal week:
    ddri_score_candidate = current_w4_ewma

structural-shock week:
    ddri_score_candidate = previous_w4_ewma
                           + 0.5 * (current_w4_ewma - previous_w4_ewma)
```

Trend, confidence, and risk remain separately observable components. They do
not directly add a bonus or penalty to `ddri_score_candidate` in this version.

## Public Schema

| Column | Meaning |
| --- | --- |
| `registered_domain` | Registered domain identity |
| `reputation_score` | Four-week EWMA level |
| `reputation_confidence` | Uncalibrated confidence heuristic in `[0, 1]` |
| `reputation_trend` | Rising, falling, stable, or uncertain |
| `trend_strength` | Signed confirmed score slope per week |
| `history_observations` | Available scored snapshots in the eight-week window |
| `structural_shock` | Whether the latest ranking-source set changed |
| `ddri_score_candidate` | Experimental shock-damped reputation candidate |
| `observed_risk` | Current or recent public risk state |
| `snapshot_date` | Latest input snapshot date |
| `ddri_methodology_version` | Version of the temporal methodology |

The history is an eight-calendar-week grid. One or two missing weekly releases
are represented as empty observations instead of compressing the time axis.
Building requires at least six releases in the grid and at least three releases
in its latest four weeks. Snapshot dates may drift by one day from their weekly
spacing; each date, ISO release tag, filename, CSV value, and consensus
methodology version must still agree.

The CSV is sorted by candidate score descending, reputation score descending,
and domain ascending. Gzip output is deterministic. Metadata records the full
calendar grid, missing tags, available input tags and SHA256 checksums, formula
parameters, output checksum, component summaries, workflow run ID, and source
commit SHA. Empty rank-band noise scales are recorded as JSON `null`.

## Local Build

Download the available releases in an eight-week calendar window into one
directory per release tag, then run:

```bash
python scripts/build_reputation_release.py \
  --release-root /tmp/ddri-release-history \
  --output-dir data/archive
```

With GitHub CLI authentication, history can be prepared automatically:

```bash
python scripts/download_release_history.py \
  --repo ivan-aleshin/composite-domain-rating \
  --release-tag data-latest \
  --count 8 \
  --output-dir /tmp/ddri-release-history
```

## Observation Policy

The parameters remain fixed while weekly component behavior is observed.
Changing a formula requires a new `ddri_methodology_version`. The first formal
review is planned after 26 homogeneous consensus snapshots; 52 snapshots are
preferred before production calibration.

The DDRI workflow installs the exact NumPy and pandas versions in
`requirements-reputation.txt`; dependency changes therefore require an
explicit repository change rather than silently changing numeric output.
