# Analysis Reports

This directory stores reproducible analysis reports that summarize published
data releases and methodology diagnostics.

Reports under `releases/` are built from public GitHub release assets only:
the derived CSV archive and its metadata JSON. They are useful for archive
shape, source coverage, risk-surface, schema, and week-over-week stability
checks.

Use `scripts/analysis/analyze_public_release.py` to generate these reports
from downloaded release assets.

They do not replace internal dbt analyses under `dbt/analyses/`. Public
release assets intentionally omit raw ranks and source-specific percentiles, so
source correlation, jackknife influence, and raw-signal checks require the
internal mart or dbt analysis queries.

Current release reports:

- [2026-05-31](./releases/2026-05-31.md)
