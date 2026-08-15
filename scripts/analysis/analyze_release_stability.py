"""Generate a multi-week stability report from public data release assets."""

from __future__ import annotations

import argparse
import gc
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from analyze_public_release import (
    COVERAGE_ORDER,
    SOURCE_NAMES,
    fmt_float,
    fmt_int,
    fmt_pct,
    load_meta,
    load_public_csv,
    markdown_table,
    source_presence,
    top_frame,
)


TOP_N_VALUES = (100, 1_000, 10_000, 100_000)


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    csv_path: Path
    meta_path: Path
    meta: dict[str, Any]

    @property
    def snapshot_date(self) -> str:
        return str(self.meta.get("snapshot_date", ""))


def discover_release_assets(release_root: Path) -> list[ReleaseAsset]:
    assets = []
    for release_dir in sorted(path for path in release_root.iterdir() if path.is_dir()):
        csv_files = sorted(release_dir.glob("domain_consensus_*.csv.gz"))
        meta_files = sorted(release_dir.glob("meta_*.json"))
        if len(csv_files) != 1 or len(meta_files) != 1:
            raise RuntimeError(
                f"Expected one CSV and one metadata JSON under {release_dir}, "
                f"found {len(csv_files)} CSV files and {len(meta_files)} metadata files",
            )
        meta = load_meta(meta_files[0])
        assets.append(
            ReleaseAsset(
                tag=release_dir.name,
                csv_path=csv_files[0],
                meta_path=meta_files[0],
                meta=meta,
            )
        )

    if not assets:
        raise RuntimeError(f"No release asset directories found under {release_root}")

    return sorted(assets, key=lambda asset: asset.snapshot_date)


def summarize_release(asset: ReleaseAsset, df: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "tag": asset.tag,
        "snapshot_date": asset.snapshot_date,
        "created_at": asset.meta.get("created_at"),
        "methodology_version": asset.meta.get("methodology_version"),
        "public_columns": tuple(asset.meta.get("public_columns", [])),
        "rows": len(df),
        "scored_rows": int(df["consensus_score"].notna().sum()),
        "sparse_rows": int((df["coverage_tier"] == "sparse").sum()),
        "risk_observed_rows": int((df["risk_sources_count"] > 0).sum()),
        "security_flagged_rows": int(df["security_flags_observed"].sum()),
        "score_mean": float(df["consensus_score"].mean()),
        "score_median": float(df["consensus_score"].median()),
        "score_p95": float(df["consensus_score"].quantile(0.95)),
    }

    for tier in COVERAGE_ORDER:
        summary[f"{tier}_rows"] = int((df["coverage_tier"] == tier).sum())

    presence = source_presence(df)
    for source in SOURCE_NAMES:
        summary[f"{source}_public_rows"] = int(presence[source].sum())

    for source, source_meta in sorted((asset.meta.get("sources") or {}).items()):
        summary[f"{source}_status"] = source_meta.get("status")
        summary[f"{source}_loaded_rows"] = source_meta.get("row_count")

    return summary


def compare_adjacent(
    previous_asset: ReleaseAsset,
    previous: pd.DataFrame,
    previous_top_domains: dict[int, set[str]],
    current_asset: ReleaseAsset,
    current: pd.DataFrame,
    current_top_domains: dict[int, set[str]],
) -> dict[str, Any]:
    previous_domains = pd.Index(previous["registered_domain"])
    current_domains = pd.Index(current["registered_domain"])
    current_in_previous = current["registered_domain"].isin(previous_domains)
    previous_in_current = previous["registered_domain"].isin(current_domains)

    result: dict[str, Any] = {
        "previous_snapshot_date": previous_asset.snapshot_date,
        "current_snapshot_date": current_asset.snapshot_date,
        "previous_tag": previous_asset.tag,
        "current_tag": current_asset.tag,
        "retained_public_domains": int(current_in_previous.sum()),
        "new_public_domains": int((~current_in_previous).sum()),
        "removed_public_domains": int((~previous_in_current).sum()),
    }

    previous_comp = previous[["registered_domain", "consensus_score", "coverage_tier", "sources_count"]]
    current_comp = current[["registered_domain", "consensus_score", "coverage_tier", "sources_count"]]
    merged = current_comp.merge(
        previous_comp,
        on="registered_domain",
        how="inner",
        suffixes=("_current", "_previous"),
        copy=False,
    )

    both_scored = merged[
        merged["consensus_score_current"].notna()
        & merged["consensus_score_previous"].notna()
    ].copy()
    both_scored["score_delta"] = both_scored["consensus_score_current"] - both_scored["consensus_score_previous"]
    both_scored["abs_score_delta"] = both_scored["score_delta"].abs()
    abs_delta = both_scored["abs_score_delta"]
    delta = both_scored["score_delta"]

    result.update(
        {
            "both_scored_retained_domains": len(both_scored),
            "mean_score_delta": float(delta.mean()) if not delta.empty else math.nan,
            "median_score_delta": float(delta.median()) if not delta.empty else math.nan,
            "mean_abs_score_delta": float(abs_delta.mean()) if not abs_delta.empty else math.nan,
            "p95_abs_score_delta": float(abs_delta.quantile(0.95)) if not abs_delta.empty else math.nan,
            "p99_abs_score_delta": float(abs_delta.quantile(0.99)) if not abs_delta.empty else math.nan,
            "max_abs_score_delta": float(abs_delta.max()) if not abs_delta.empty else math.nan,
        }
    )

    for n in TOP_N_VALUES:
        previous_top = previous_top_domains[n]
        current_top = current_top_domains[n]
        result[f"top_{n}_retained"] = len(previous_top & current_top)
        result[f"top_{n}_retention"] = len(previous_top & current_top) / n

    transition = pd.crosstab(merged["coverage_tier_previous"], merged["coverage_tier_current"])
    for previous_tier in COVERAGE_ORDER:
        for current_tier in COVERAGE_ORDER:
            result[f"coverage_{previous_tier}_to_{current_tier}"] = int(
                transition.get(current_tier, pd.Series(dtype="int64")).get(previous_tier, 0)
            )

    return result


def top_domain_sets(df: pd.DataFrame) -> dict[int, set[str]]:
    largest_n = max(TOP_N_VALUES)
    top_domains = list(top_frame(df, largest_n)["registered_domain"])
    return {n: set(top_domains[:n]) for n in TOP_N_VALUES}


def rows_from_summaries(summaries: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for summary in summaries:
        rows.append(
            [
                summary["snapshot_date"],
                summary["tag"],
                fmt_int(summary["rows"]),
                fmt_int(summary["scored_rows"]),
                fmt_pct(summary["scored_rows"], summary["rows"]),
                fmt_int(summary["full_rows"]),
                fmt_int(summary["high_rows"]),
                fmt_int(summary["partial_rows"]),
                fmt_int(summary["sparse_rows"]),
                fmt_int(summary["security_flagged_rows"]),
                str(summary["methodology_version"]),
            ]
        )
    return rows


def source_health_rows(summaries: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    source_names = sorted(
        {
            key.removesuffix("_status")
            for summary in summaries
            for key in summary
            if key.endswith("_status")
        }
    )
    for source in source_names:
        statuses = [summary.get(f"{source}_status", "") for summary in summaries]
        loaded_rows = [summary.get(f"{source}_loaded_rows") for summary in summaries]
        rows.append(
            [
                source,
                ", ".join(sorted({str(status) for status in statuses if status})),
                fmt_int(min(value for value in loaded_rows if value is not None)),
                fmt_int(max(value for value in loaded_rows if value is not None)),
                fmt_float(pd.Series([value for value in loaded_rows if value is not None]).mean(), 0),
            ]
        )
    return rows


def adjacent_rows(comparisons: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for comparison in comparisons:
        rows.append(
            [
                f"{comparison['previous_snapshot_date']} -> {comparison['current_snapshot_date']}",
                fmt_int(comparison["new_public_domains"]),
                fmt_int(comparison["removed_public_domains"]),
                fmt_pct(
                    comparison["retained_public_domains"],
                    comparison["retained_public_domains"] + comparison["removed_public_domains"],
                ),
                fmt_pct(comparison["top_100_retention"], 1),
                fmt_pct(comparison["top_1000_retention"], 1),
                fmt_pct(comparison["top_10000_retention"], 1),
                fmt_pct(comparison["top_100000_retention"], 1),
                fmt_float(comparison["mean_abs_score_delta"], 4),
                fmt_float(comparison["p95_abs_score_delta"], 4),
                fmt_float(comparison["p99_abs_score_delta"], 4),
            ]
        )
    return rows


def aggregate_rows(summaries: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> list[list[str]]:
    summary_df = pd.DataFrame(summaries)
    comparison_df = pd.DataFrame(comparisons)
    return [
        ["snapshots", fmt_int(len(summaries))],
        ["date range", f"{summaries[0]['snapshot_date']} to {summaries[-1]['snapshot_date']}"],
        ["public rows min/max", f"{fmt_int(summary_df['rows'].min())} / {fmt_int(summary_df['rows'].max())}"],
        ["public rows range", fmt_int(summary_df["rows"].max() - summary_df["rows"].min())],
        ["scored rows min/max", f"{fmt_int(summary_df['scored_rows'].min())} / {fmt_int(summary_df['scored_rows'].max())}"],
        ["full rows min/max", f"{fmt_int(summary_df['full_rows'].min())} / {fmt_int(summary_df['full_rows'].max())}"],
        [
            "security flags min/max",
            f"{fmt_int(summary_df['security_flagged_rows'].min())} / "
            f"{fmt_int(summary_df['security_flagged_rows'].max())}",
        ],
        [
            "top-100 retention min/median",
            f"{fmt_pct(comparison_df['top_100_retention'].min(), 1)} / "
            f"{fmt_pct(comparison_df['top_100_retention'].median(), 1)}",
        ],
        [
            "top-1k retention min/median",
            f"{fmt_pct(comparison_df['top_1000_retention'].min(), 1)} / "
            f"{fmt_pct(comparison_df['top_1000_retention'].median(), 1)}",
        ],
        [
            "top-10k retention min/median",
            f"{fmt_pct(comparison_df['top_10000_retention'].min(), 1)} / "
            f"{fmt_pct(comparison_df['top_10000_retention'].median(), 1)}",
        ],
        ["mean abs score drift median", fmt_float(comparison_df["mean_abs_score_delta"].median(), 4)],
        ["p95 abs score drift median", fmt_float(comparison_df["p95_abs_score_delta"].median(), 4)],
    ]


def schema_rows(summaries: list[dict[str, Any]]) -> list[list[str]]:
    unique_schemas = {}
    for summary in summaries:
        unique_schemas.setdefault(summary["public_columns"], []).append(summary["snapshot_date"])
    rows = []
    for columns, dates in unique_schemas.items():
        rows.append([f"{dates[0]} to {dates[-1]}", fmt_int(len(dates)), ", ".join(columns)])
    return rows


def latest_source_combination_rows(latest: pd.DataFrame) -> list[list[str]]:
    grouped = (
        latest.groupby("ranking_sources_present", dropna=False)
        .agg(
            rows=("registered_domain", "size"),
            scored_rows=("consensus_score", lambda values: int(values.notna().sum())),
            avg_score=("consensus_score", "mean"),
            security_flags=("security_flags_observed", "sum"),
        )
        .reset_index()
        .sort_values(["rows", "ranking_sources_present"], ascending=[False, True])
        .head(20)
    )
    return [
        [
            row["ranking_sources_present"] or "(none)",
            fmt_int(row["rows"]),
            fmt_pct(row["rows"], len(latest)),
            fmt_int(row["scored_rows"]),
            fmt_float(row["avg_score"], 3),
            fmt_int(row["security_flags"]),
        ]
        for _, row in grouped.iterrows()
    ]


def latest_risk_rows(latest: pd.DataFrame) -> list[list[str]]:
    rows = []
    for risk_sources_count in sorted(latest["risk_sources_count"].unique()):
        subset = latest[latest["risk_sources_count"] == risk_sources_count]
        rows.append(
            [
                str(int(risk_sources_count)),
                fmt_int(len(subset)),
                fmt_pct(len(subset), len(latest)),
                fmt_int(subset["consensus_score"].notna().sum()),
                fmt_float(subset["consensus_score"].mean(), 3),
            ]
        )
    return rows


def build_report(
    assets: list[ReleaseAsset],
    summaries: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    latest: pd.DataFrame,
    output: Path,
) -> str:
    latest_summary = summaries[-1]
    comparison_df = pd.DataFrame(comparisons)
    release_labels = ", ".join(asset.tag for asset in assets)
    top_100_min = comparison_df["top_100_retention"].min()
    top_1000_min = comparison_df["top_1000_retention"].min()
    p95_drift_median = comparison_df["p95_abs_score_delta"].median()
    schema_count = len({summary["public_columns"] for summary in summaries})
    non_fresh_sources = sorted(
        {
            f"{key.removesuffix('_status')}={value}"
            for summary in summaries
            for key, value in summary.items()
            if key.endswith("_status") and value not in {None, "", "fresh"}
        }
    )

    readiness = "green"
    if top_100_min < 0.95 or top_1000_min < 0.95 or p95_drift_median > 10 or schema_count > 1 or non_fresh_sources:
        readiness = "yellow"
    if top_100_min < 0.90 or top_1000_min < 0.90 or p95_drift_median > 20:
        readiness = "red"

    report = [
        f"# Release Stability Analysis - {latest_summary['snapshot_date']}",
        "",
        f"Generated at `{datetime.now(timezone.utc).isoformat()}` from public GitHub release assets.",
        f"Release window: `{assets[0].snapshot_date}` to `{assets[-1].snapshot_date}`.",
        f"Release tags: `{release_labels}`.",
        "",
        "## Executive Summary",
        "",
        f"- Stability readiness: **{readiness}**.",
        f"- Latest public archive rows: **{fmt_int(latest_summary['rows'])}**; scored rows: "
        f"**{fmt_int(latest_summary['scored_rows'])}** "
        f"({fmt_pct(latest_summary['scored_rows'], latest_summary['rows'])}).",
        f"- Public row-count range across the window: "
        f"**{fmt_int(max(summary['rows'] for summary in summaries) - min(summary['rows'] for summary in summaries))}** rows.",
        f"- Top-100 weekly retention min/median: "
        f"**{fmt_pct(comparison_df['top_100_retention'].min(), 1)} / "
        f"{fmt_pct(comparison_df['top_100_retention'].median(), 1)}**.",
        f"- Top-1k weekly retention min/median: "
        f"**{fmt_pct(comparison_df['top_1000_retention'].min(), 1)} / "
        f"{fmt_pct(comparison_df['top_1000_retention'].median(), 1)}**.",
        f"- Median p95 absolute score drift: **{fmt_float(p95_drift_median, 4)}**.",
        f"- Public schema variants observed: **{schema_count}**.",
        f"- Non-fresh source statuses observed: **{', '.join(non_fresh_sources) if non_fresh_sources else 'none'}**.",
        "",
        "## Window Summary",
        "",
        markdown_table(["Metric", "Value"], aggregate_rows(summaries, comparisons)),
        "",
        "## Snapshot Trend",
        "",
        markdown_table(
            [
                "Snapshot",
                "Tag",
                "Rows",
                "Scored",
                "Scored share",
                "Full",
                "High",
                "Partial",
                "Sparse",
                "Security flags",
                "Methodology",
            ],
            rows_from_summaries(summaries),
        ),
        "",
        "## Source Health",
        "",
        markdown_table(
            ["Source", "Statuses", "Loaded rows min", "Loaded rows max", "Loaded rows mean"],
            source_health_rows(summaries),
        ),
        "",
        "## Public Schema Stability",
        "",
        markdown_table(["Date range", "Snapshots", "Columns"], schema_rows(summaries)),
        "",
        "## Week-Over-Week Stability",
        "",
        markdown_table(
            [
                "Window",
                "New domains",
                "Removed domains",
                "Public retention",
                "Top 100",
                "Top 1k",
                "Top 10k",
                "Top 100k",
                "Mean abs drift",
                "P95 abs drift",
                "P99 abs drift",
            ],
            adjacent_rows(comparisons),
        ),
        "",
        "## Latest Source Combination Shape",
        "",
        markdown_table(
            ["Sources present", "Rows", "Share", "Scored rows", "Avg score", "Security flags"],
            latest_source_combination_rows(latest),
        ),
        "",
        "## Latest Risk-Layer Surface",
        "",
        markdown_table(
            ["Risk sources count", "Rows", "Share", "Scored rows", "Mean score"],
            latest_risk_rows(latest),
        ),
        "",
        "## Interpretation",
        "",
        "- This report uses public release assets only. It does not inspect raw source ranks or source-specific percentiles.",
        "- Top-N retention and score drift are measured only between adjacent public snapshots.",
        "- Risk fields remain observation-oriented and do not change `consensus_score`.",
        "- If this window remains representative, the project is in a reasonable state for a release-candidate discussion; "
        "source-percentile correlation and jackknife should still be refreshed from the internal mart before a final "
        "`v1.0.0` decision.",
        "",
        "## Reproduction",
        "",
        "The input assets were downloaded under `/tmp/cdr-stability` with `gh release download` for tags "
        "`data-2026-W23` through `data-2026-W32`.",
        "",
        "```bash",
        "python scripts/analysis/analyze_release_stability.py \\",
        "  --release-root /tmp/cdr-stability \\",
        f"  --output {output}",
        "```",
        "",
    ]
    return "\n".join(report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-root",
        type=Path,
        required=True,
        help="Directory containing one subdirectory per release tag",
    )
    parser.add_argument("--output", type=Path, required=True, help="Markdown report output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assets = discover_release_assets(args.release_root)
    summaries = []
    comparisons = []
    previous_asset: ReleaseAsset | None = None
    previous_df: pd.DataFrame | None = None
    previous_top_domains: dict[int, set[str]] | None = None
    latest_df: pd.DataFrame | None = None

    for asset in assets:
        print(f"Loading {asset.tag} ({asset.snapshot_date}) from {asset.csv_path}", flush=True)
        current_df = load_public_csv(asset.csv_path)
        print(f"Summarizing {asset.tag}", flush=True)
        summaries.append(summarize_release(asset, current_df))
        current_top_domains = top_domain_sets(current_df)
        if previous_asset is not None and previous_df is not None:
            if previous_top_domains is None:
                raise RuntimeError("Missing top-domain cache for previous release")
            print(f"Comparing {previous_asset.tag} -> {asset.tag}", flush=True)
            comparisons.append(
                compare_adjacent(
                    previous_asset,
                    previous_df,
                    previous_top_domains,
                    asset,
                    current_df,
                    current_top_domains,
                )
            )
            latest_df = None
            del previous_df
            gc.collect()
        previous_asset = asset
        previous_df = current_df
        previous_top_domains = current_top_domains
        latest_df = current_df

    if latest_df is None:
        raise RuntimeError("No latest dataframe loaded")
    if not comparisons:
        raise RuntimeError("At least two releases are required for stability analysis")

    report = build_report(assets, summaries, comparisons, latest_df, args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
