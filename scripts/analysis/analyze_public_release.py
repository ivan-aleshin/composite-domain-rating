"""Generate public-release diagnostics from published data assets.

The public CSV intentionally excludes raw source ranks and source-specific
percentiles, so this report focuses on archive-level diagnostics that can be
reproduced from release assets alone.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SOURCE_NAMES = ("tranco", "majestic", "radar", "crux", "opr")
COVERAGE_ORDER = ("full", "high", "partial", "sparse")
PUBLIC_USECOLS = {
    "registered_domain",
    "consensus_score",
    "coverage_tier",
    "sources_count",
    "ranking_sources_present",
    "security_flags_observed",
    "risk_sources_count",
    "threat_types",
    "snapshot_date",
    "methodology_version",
}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def load_meta(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_public_csv(path: Path) -> pd.DataFrame:
    dtype = {
        "registered_domain": "string",
        "coverage_tier": "string",
        "ranking_sources_present": "string",
        "threat_types": "string",
        "snapshot_date": "string",
        "methodology_version": "string",
    }
    df = pd.read_csv(
        path,
        compression="gzip",
        usecols=lambda column: column in PUBLIC_USECOLS,
        dtype=dtype,
        converters={"security_flags_observed": parse_bool},
    )

    defaults: dict[str, Any] = {
        "consensus_score": math.nan,
        "coverage_tier": "",
        "sources_count": 0,
        "ranking_sources_present": "",
        "security_flags_observed": False,
        "risk_sources_count": 0,
        "threat_types": "",
        "snapshot_date": "",
        "methodology_version": "",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default

    df["consensus_score"] = pd.to_numeric(df["consensus_score"], errors="coerce")
    df["sources_count"] = pd.to_numeric(df["sources_count"], errors="coerce").fillna(0).astype("int8")
    df["risk_sources_count"] = pd.to_numeric(df["risk_sources_count"], errors="coerce").fillna(0).astype("int8")
    df["security_flags_observed"] = df["security_flags_observed"].map(parse_bool).astype(bool)

    for column in ("coverage_tier", "ranking_sources_present", "threat_types", "snapshot_date", "methodology_version"):
        df[column] = df[column].fillna("").astype("string")

    return df


def fmt_int(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{int(value):,}"


def fmt_float(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number):
        return ""
    return f"{number:,.{digits}f}"


def fmt_pct(numerator: Any, denominator: Any, digits: int = 2) -> str:
    if not denominator:
        return ""
    return f"{float(numerator) / float(denominator) * 100:.{digits}f}%"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def top_frame(df: pd.DataFrame, n: int) -> pd.DataFrame:
    scored = df[df["consensus_score"].notna()]
    return scored.sort_values(
        ["consensus_score", "sources_count", "registered_domain"],
        ascending=[False, False, True],
    ).head(n)


def source_presence(df: pd.DataFrame) -> pd.DataFrame:
    presence = df["ranking_sources_present"].fillna("").str.get_dummies(sep=",")
    for source in SOURCE_NAMES:
        if source not in presence.columns:
            presence[source] = 0
    return presence.loc[:, list(SOURCE_NAMES)].astype(bool)


def score_quantile_rows(scores: pd.Series) -> list[list[str]]:
    quantiles = [
        ("min", 0.0),
        ("p01", 0.01),
        ("p05", 0.05),
        ("p10", 0.10),
        ("p25", 0.25),
        ("p50", 0.50),
        ("p75", 0.75),
        ("p90", 0.90),
        ("p95", 0.95),
        ("p99", 0.99),
        ("max", 1.0),
    ]
    return [[label, fmt_float(scores.quantile(q), 4)] for label, q in quantiles]


def coverage_rows(df: pd.DataFrame) -> list[list[str]]:
    total = len(df)
    rows = []
    for tier in COVERAGE_ORDER:
        subset = df[df["coverage_tier"] == tier]
        scores = subset["consensus_score"].dropna()
        rows.append(
            [
                tier,
                fmt_int(len(subset)),
                fmt_pct(len(subset), total),
                fmt_int(scores.size),
                fmt_float(scores.mean(), 3),
                fmt_float(scores.median(), 3),
                fmt_float(scores.quantile(0.95), 3) if not scores.empty else "",
            ]
        )
    return rows


def source_count_rows(df: pd.DataFrame) -> list[list[str]]:
    total = len(df)
    rows = []
    for sources_count in sorted(df["sources_count"].unique(), reverse=True):
        subset = df[df["sources_count"] == sources_count]
        scores = subset["consensus_score"].dropna()
        rows.append(
            [
                str(int(sources_count)),
                fmt_int(len(subset)),
                fmt_pct(len(subset), total),
                fmt_int(scores.size),
                fmt_float(scores.mean(), 3),
                fmt_float(scores.median(), 3),
            ]
        )
    return rows


def score_band_rows(df: pd.DataFrame) -> list[list[str]]:
    scores = df["consensus_score"].dropna()
    bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100.0000001]
    labels = ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100"]
    bands = pd.cut(scores, bins=bins, labels=labels, right=False, include_lowest=True)
    counts = bands.value_counts(sort=False)
    rows = [
        [
            label,
            fmt_int(int(counts.get(label, 0))),
            fmt_pct(int(counts.get(label, 0)), len(scores)),
            fmt_pct(int(counts.get(label, 0)), len(df)),
        ]
        for label in labels
    ]
    rows.append(
        [
            "NULL",
            fmt_int(df["consensus_score"].isna().sum()),
            "",
            fmt_pct(df["consensus_score"].isna().sum(), len(df)),
        ]
    )
    return rows


def source_summary_rows(df: pd.DataFrame) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    presence = source_presence(df)
    total = len(df)

    source_rows = []
    for source in SOURCE_NAMES:
        count = int(presence[source].sum())
        source_rows.append([source, fmt_int(count), fmt_pct(count, total)])

    pair_rows = []
    for idx, left in enumerate(SOURCE_NAMES):
        for right in SOURCE_NAMES[idx + 1 :]:
            both = int((presence[left] & presence[right]).sum())
            union = int((presence[left] | presence[right]).sum())
            pair_rows.append(
                [
                    f"{left}+{right}",
                    fmt_int(both),
                    fmt_pct(both, total),
                    fmt_pct(both, union),
                ]
            )
    pair_rows.sort(key=lambda row: int(row[1].replace(",", "")), reverse=True)

    enriched = df.assign(
        is_scored=df["consensus_score"].notna(),
        has_risk_observation=df["risk_sources_count"] > 0,
    )
    grouped = (
        enriched.groupby("ranking_sources_present", dropna=False)
        .agg(
            rows=("registered_domain", "size"),
            scored_rows=("is_scored", "sum"),
            avg_score=("consensus_score", "mean"),
            security_flags=("security_flags_observed", "sum"),
            risk_observed=("has_risk_observation", "sum"),
        )
        .reset_index()
        .sort_values(["rows", "ranking_sources_present"], ascending=[False, True])
    )
    combo_rows = []
    for _, row in grouped.iterrows():
        combo_rows.append(
            [
                row["ranking_sources_present"] or "(none)",
                fmt_int(row["rows"]),
                fmt_pct(row["rows"], total),
                fmt_int(row["scored_rows"]),
                fmt_float(row["avg_score"], 3),
                fmt_int(row["risk_observed"]),
                fmt_int(row["security_flags"]),
            ]
        )

    return source_rows, pair_rows, combo_rows


def risk_rows(df: pd.DataFrame) -> tuple[list[list[str]], list[list[str]], list[list[str]], list[list[str]]]:
    total = len(df)
    distribution_rows = []
    for risk_sources_count in sorted(df["risk_sources_count"].unique()):
        subset = df[df["risk_sources_count"] == risk_sources_count]
        distribution_rows.append(
            [
                str(int(risk_sources_count)),
                fmt_int(len(subset)),
                fmt_pct(len(subset), total),
                fmt_float(subset["consensus_score"].mean(), 3),
                fmt_int(subset["consensus_score"].notna().sum()),
            ]
        )

    by_coverage_rows = []
    for tier in COVERAGE_ORDER:
        subset = df[df["coverage_tier"] == tier]
        if subset.empty:
            continue
        any_risk = int((subset["risk_sources_count"] > 0).sum())
        security_flags = int(subset["security_flags_observed"].sum())
        by_coverage_rows.append(
            [
                tier,
                fmt_int(len(subset)),
                fmt_int(any_risk),
                fmt_pct(any_risk, len(subset)),
                fmt_int(security_flags),
                fmt_pct(security_flags, len(subset)),
            ]
        )

    threat_values = (
        df.loc[df["threat_types"].fillna("").ne(""), "threat_types"]
        .str.split("|")
        .explode()
        .dropna()
    )
    threat_counts = threat_values.value_counts()
    threat_rows = [
        [str(threat_type), fmt_int(count), fmt_pct(count, max(1, int((df["risk_sources_count"] > 0).sum())))]
        for threat_type, count in threat_counts.head(20).items()
    ]

    risky_top = df[df["security_flags_observed"]].sort_values(
        ["consensus_score", "sources_count", "registered_domain"],
        ascending=[False, False, True],
    ).head(25)
    risky_rows = [
        [
            row["registered_domain"],
            fmt_float(row["consensus_score"], 4),
            row["coverage_tier"],
            int(row["sources_count"]),
            int(row["risk_sources_count"]),
            row["threat_types"] or "",
        ]
        for _, row in risky_top.iterrows()
    ]

    return distribution_rows, by_coverage_rows, threat_rows, risky_rows


def rightmost_label_rows(df: pd.DataFrame) -> list[list[str]]:
    labels = df["registered_domain"].str.rsplit(".", n=1).str[-1].fillna("")
    total_counts = labels.value_counts()
    scored_counts = labels[df["consensus_score"].notna()].value_counts()
    security_counts = labels[df["security_flags_observed"]].value_counts()
    rows = []
    for label, count in total_counts.head(25).items():
        rows.append(
            [
                str(label),
                fmt_int(count),
                fmt_pct(count, len(df)),
                fmt_int(scored_counts.get(label, 0)),
                fmt_int(security_counts.get(label, 0)),
            ]
        )
    return rows


def source_status_rows(meta: dict[str, Any]) -> list[list[str]]:
    rows = []
    for source, payload in sorted(meta.get("sources", {}).items()):
        source_metadata = payload.get("source_metadata") or {}
        rows.append(
            [
                source,
                payload.get("status", ""),
                payload.get("load_status", ""),
                fmt_int(payload.get("row_count")),
                fmt_int(source_metadata.get("row_count")),
                fmt_int(source_metadata.get("valid_row_count")),
                fmt_int(source_metadata.get("duplicate_registered_domains")),
                fmt_int(source_metadata.get("skipped_rows")),
                str(payload.get("age_days", "")),
            ]
        )
    return rows


def row_count_delta_rows(current: pd.DataFrame, previous: pd.DataFrame) -> list[list[str]]:
    metrics = [
        ("public rows", len(current), len(previous)),
        ("scored rows", int(current["consensus_score"].notna().sum()), int(previous["consensus_score"].notna().sum())),
        ("sparse rows", int((current["coverage_tier"] == "sparse").sum()), int((previous["coverage_tier"] == "sparse").sum())),
        ("risk observed rows", int((current["risk_sources_count"] > 0).sum()), int((previous["risk_sources_count"] > 0).sum())),
        ("security-flagged rows", int(current["security_flags_observed"].sum()), int(previous["security_flags_observed"].sum())),
    ]
    rows = []
    for label, current_value, previous_value in metrics:
        delta = current_value - previous_value
        rows.append(
            [
                label,
                fmt_int(current_value),
                fmt_int(previous_value),
                f"{delta:+,}",
                fmt_pct(delta, previous_value) if previous_value else "",
            ]
        )
    return rows


def release_comparison_rows(current: pd.DataFrame, previous: pd.DataFrame) -> tuple[list[list[str]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    previous_domains = pd.Index(previous["registered_domain"])
    current_domains = pd.Index(current["registered_domain"])
    current_in_previous = current["registered_domain"].isin(previous_domains)
    previous_in_current = previous["registered_domain"].isin(current_domains)

    added = current.loc[~current_in_previous]
    removed = previous.loc[~previous_in_current]
    retained = int(current_in_previous.sum())
    rows = [
        ["retained public domains", fmt_int(retained), fmt_pct(retained, len(previous))],
        ["new public domains", fmt_int(len(added)), fmt_pct(len(added), len(current))],
        ["removed public domains", fmt_int(len(removed)), fmt_pct(len(removed), len(previous))],
        [
            "new scored domains",
            fmt_int(added["consensus_score"].notna().sum()),
            fmt_pct(added["consensus_score"].notna().sum(), max(1, current["consensus_score"].notna().sum())),
        ],
        [
            "removed scored domains",
            fmt_int(removed["consensus_score"].notna().sum()),
            fmt_pct(removed["consensus_score"].notna().sum(), max(1, previous["consensus_score"].notna().sum())),
        ],
    ]
    return rows, added, removed, current.loc[current_in_previous]


def transition_table(
    merged: pd.DataFrame,
    previous_column: str,
    current_column: str,
    previous_values: list[Any],
    current_values: list[Any],
) -> list[list[str]]:
    matrix = pd.crosstab(merged[previous_column], merged[current_column])
    matrix = matrix.reindex(index=previous_values, columns=current_values, fill_value=0)
    rows = []
    for previous_value in previous_values:
        rows.append([str(previous_value)] + [fmt_int(matrix.loc[previous_value, current_value]) for current_value in current_values])
    return rows


def score_drift_rows(merged: pd.DataFrame) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    both_scored = merged[
        merged["consensus_score_current"].notna()
        & merged["consensus_score_previous"].notna()
    ].copy()
    both_scored["score_delta"] = both_scored["consensus_score_current"] - both_scored["consensus_score_previous"]
    both_scored["abs_score_delta"] = both_scored["score_delta"].abs()

    drift = both_scored["score_delta"]
    abs_drift = both_scored["abs_score_delta"]
    summary_rows = [
        ["both-scored retained domains", fmt_int(len(both_scored))],
        ["mean delta", fmt_float(drift.mean(), 4)],
        ["median delta", fmt_float(drift.median(), 4)],
        ["mean absolute delta", fmt_float(abs_drift.mean(), 4)],
        ["p95 absolute delta", fmt_float(abs_drift.quantile(0.95), 4)],
        ["p99 absolute delta", fmt_float(abs_drift.quantile(0.99), 4)],
        ["max absolute delta", fmt_float(abs_drift.max(), 4)],
    ]

    mover_columns = [
        "registered_domain",
        "consensus_score_current",
        "consensus_score_previous",
        "score_delta",
        "coverage_tier_current",
        "coverage_tier_previous",
        "sources_count_current",
        "sources_count_previous",
    ]
    upward = both_scored.nlargest(20, "score_delta")[mover_columns]
    downward = both_scored.nsmallest(20, "score_delta")[mover_columns]

    def mover_rows(frame: pd.DataFrame) -> list[list[str]]:
        return [
            [
                row["registered_domain"],
                fmt_float(row["consensus_score_current"], 4),
                fmt_float(row["consensus_score_previous"], 4),
                fmt_float(row["score_delta"], 4),
                row["coverage_tier_current"],
                row["coverage_tier_previous"],
                int(row["sources_count_current"]),
                int(row["sources_count_previous"]),
            ]
            for _, row in frame.iterrows()
        ]

    return summary_rows, mover_rows(upward), mover_rows(downward)


def top_n_retention_rows(current: pd.DataFrame, previous: pd.DataFrame) -> tuple[list[list[str]], list[list[str]]]:
    rows = []
    entrant_rows: list[list[str]] = []
    for n in (100, 1_000, 10_000, 100_000):
        current_top = top_frame(current, n)
        previous_top = top_frame(previous, n)
        previous_set = set(previous_top["registered_domain"])
        current_set = set(current_top["registered_domain"])
        retained = len(current_set & previous_set)
        rows.append([fmt_int(n), fmt_int(retained), fmt_pct(retained, n), fmt_int(n - retained)])
        if n == 100:
            entrants = current_top[~current_top["registered_domain"].isin(previous_set)].head(20)
            entrant_rows = [
                [
                    row["registered_domain"],
                    fmt_float(row["consensus_score"], 4),
                    row["coverage_tier"],
                    int(row["sources_count"]),
                    row["ranking_sources_present"],
                ]
                for _, row in entrants.iterrows()
            ]
    return rows, entrant_rows


def release_compare_section(current: pd.DataFrame, previous: pd.DataFrame, previous_meta: dict[str, Any]) -> list[str]:
    rows, added, removed, _ = release_comparison_rows(current, previous)
    current_comp = current[
        ["registered_domain", "consensus_score", "coverage_tier", "sources_count", "ranking_sources_present"]
    ].copy()
    previous_comp = previous[
        ["registered_domain", "consensus_score", "coverage_tier", "sources_count", "ranking_sources_present"]
    ].copy()
    merged = current_comp.merge(
        previous_comp,
        on="registered_domain",
        how="inner",
        suffixes=("_current", "_previous"),
        copy=False,
    )

    score_summary, upward_rows, downward_rows = score_drift_rows(merged)
    retention_rows, top100_entrant_rows = top_n_retention_rows(current, previous)

    added_scored_rows = [
        [
            row["registered_domain"],
            fmt_float(row["consensus_score"], 4),
            row["coverage_tier"],
            int(row["sources_count"]),
            row["ranking_sources_present"],
        ]
        for _, row in top_frame(added, 20).iterrows()
    ]
    removed_scored_rows = [
        [
            row["registered_domain"],
            fmt_float(row["consensus_score"], 4),
            row["coverage_tier"],
            int(row["sources_count"]),
            row["ranking_sources_present"],
        ]
        for _, row in top_frame(removed, 20).iterrows()
    ]

    previous_snapshot = previous_meta.get("snapshot_date", "previous")
    return [
        "## Week-Over-Week Public Archive Change",
        "",
        f"Previous comparison snapshot: `{previous_snapshot}`.",
        "",
        markdown_table(["Metric", "Current", "Previous", "Delta", "Delta %"], row_count_delta_rows(current, previous)),
        "",
        markdown_table(["Domain set metric", "Rows", "Rate"], rows),
        "",
        "### Top-N Retention",
        "",
        markdown_table(["Top N", "Retained", "Retention", "Entrants/exits"], retention_rows),
        "",
        "### Score Drift For Retained Scored Domains",
        "",
        markdown_table(["Metric", "Value"], score_summary),
        "",
        "### Coverage Tier Transitions",
        "",
        markdown_table(["previous -> current", *COVERAGE_ORDER], transition_table(
            merged,
            "coverage_tier_previous",
            "coverage_tier_current",
            list(COVERAGE_ORDER),
            list(COVERAGE_ORDER),
        )),
        "",
        "### Sources Count Transitions",
        "",
        markdown_table(["previous -> current", "2", "3", "4", "5"], transition_table(
            merged,
            "sources_count_previous",
            "sources_count_current",
            [2, 3, 4, 5],
            [2, 3, 4, 5],
        )),
        "",
        "### Top Current Top-100 Entrants",
        "",
        markdown_table(["Domain", "Score", "Tier", "Sources", "Sources present"], top100_entrant_rows),
        "",
        "### Highest-Scoring New Public Domains",
        "",
        markdown_table(["Domain", "Score", "Tier", "Sources", "Sources present"], added_scored_rows),
        "",
        "### Highest-Scoring Removed Public Domains",
        "",
        markdown_table(["Domain", "Score", "Tier", "Sources", "Sources present"], removed_scored_rows),
        "",
        "### Largest Upward Score Movers",
        "",
        markdown_table(
            ["Domain", "Current", "Previous", "Delta", "Tier now", "Tier prev", "Sources now", "Sources prev"],
            upward_rows,
        ),
        "",
        "### Largest Downward Score Movers",
        "",
        markdown_table(
            ["Domain", "Current", "Previous", "Delta", "Tier now", "Tier prev", "Sources now", "Sources prev"],
            downward_rows,
        ),
        "",
    ]


def build_report(
    current: pd.DataFrame,
    current_meta: dict[str, Any],
    previous: pd.DataFrame | None,
    previous_meta: dict[str, Any] | None,
    current_csv: Path,
    previous_csv: Path | None,
    current_label: str | None,
    previous_label: str | None,
    current_meta_label: str | None,
    previous_meta_label: str | None,
    current_url: str | None,
    current_meta_url: str | None,
    previous_url: str | None,
    previous_meta_url: str | None,
) -> str:
    total = len(current)
    scored_rows = int(current["consensus_score"].notna().sum())
    sparse_rows = int((current["coverage_tier"] == "sparse").sum())
    risk_observed_rows = int((current["risk_sources_count"] > 0).sum())
    security_flag_rows = int(current["security_flags_observed"].sum())
    source_rows, pair_rows, combo_rows = source_summary_rows(current)
    risk_distribution_rows, risk_by_coverage_rows, threat_rows, risky_top_rows = risk_rows(current)

    current_snapshot = current_meta.get("snapshot_date", "")
    release_tag = (current_meta.get("release") or {}).get("tag", "")
    methodology_version = current_meta.get("methodology_version", "")
    public_columns = current_meta.get("public_columns", [])
    current_schema = ", ".join(f"`{column}`" for column in public_columns)
    current_input = current_label or str(current_csv)
    previous_input = previous_label or (str(previous_csv) if previous_csv is not None else "")
    current_meta_input = current_meta_label or f"{release_tag}/meta"
    previous_release_tag = previous_meta.get("release", {}).get("tag", "") if previous_meta else ""
    previous_meta_input = previous_meta_label or (f"{previous_release_tag}/meta" if previous_meta else "")

    report: list[str] = [
        f"# Public Release Diagnostics - {current_snapshot}",
        "",
        f"Generated at `{datetime.now(timezone.utc).isoformat()}` from `{current_input}`.",
        f"Release tag: `{release_tag}`. Methodology version: `{methodology_version}`.",
        "",
        "## Executive Summary",
        "",
        f"- Public archive rows: **{fmt_int(total)}**.",
        f"- Scored rows: **{fmt_int(scored_rows)}** ({fmt_pct(scored_rows, total)}). Sparse public rows: **{fmt_int(sparse_rows)}** ({fmt_pct(sparse_rows, total)}).",
        f"- Rows with any risk observation: **{fmt_int(risk_observed_rows)}** ({fmt_pct(risk_observed_rows, total)}). Rows meeting the public security flag threshold: **{fmt_int(security_flag_rows)}** ({fmt_pct(security_flag_rows, total)}).",
        "- The public CSV is sufficient for release-shape, coverage, source-combination, risk-surface, and week-over-week archive diagnostics.",
        "- It is not sufficient for source percentile correlation or jackknife influence, because raw source ranks and source percentile columns are intentionally omitted from public assets.",
        "",
        "## Release Metadata",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["snapshot_date", current_meta.get("snapshot_date", "")],
                ["created_at", current_meta.get("created_at", "")],
                ["row_count in metadata", fmt_int(current_meta.get("row_count"))],
                ["row_count read from CSV", fmt_int(total)],
                ["methodology_version", methodology_version],
                ["release tag", release_tag],
                ["mart table", current_meta.get("mart_table", "")],
                ["archive inclusion", (current_meta.get("archive_policy") or {}).get("included", "")],
            ],
        ),
        "",
        "### Input Assets",
        "",
        markdown_table(
            ["Role", "Label", "URL"],
            [
                ["current csv", current_input, current_url or ""],
                ["current metadata", current_meta_input, current_meta_url or ""],
                ["previous csv", previous_input, previous_url or ""],
                ["previous metadata", previous_meta_input, previous_meta_url or ""],
            ],
        ),
        "",
        "Public columns:",
        "",
        current_schema,
        "",
        "### Source Statuses",
        "",
        markdown_table(
            [
                "Source",
                "Status",
                "Load status",
                "Loaded rows",
                "Raw rows",
                "Valid rows",
                "Duplicate registered domains",
                "Skipped rows",
                "Age days",
            ],
            source_status_rows(current_meta),
        ),
        "",
        "## Public Archive Shape",
        "",
        markdown_table(
            ["Coverage tier", "Rows", "Share", "Scored rows", "Mean score", "Median score", "P95 score"],
            coverage_rows(current),
        ),
        "",
        markdown_table(
            ["Sources count", "Rows", "Share", "Scored rows", "Mean score", "Median score"],
            source_count_rows(current),
        ),
        "",
        "### Consensus Score Quantiles",
        "",
        markdown_table(["Quantile", "Score"], score_quantile_rows(current["consensus_score"].dropna())),
        "",
        "### Consensus Score Bands",
        "",
        markdown_table(["Band", "Rows", "Share of scored rows", "Share of archive"], score_band_rows(current)),
        "",
        "## Ranking Source Coverage",
        "",
        "### Individual Source Presence In Public Archive",
        "",
        markdown_table(["Source", "Rows", "Share"], source_rows),
        "",
        "### Pairwise Source Co-Occurrence",
        "",
        markdown_table(["Pair", "Rows", "Share of archive", "Jaccard within public archive"], pair_rows),
        "",
        "### Source Combination Breakdown",
        "",
        markdown_table(
            ["Sources present", "Rows", "Share", "Scored rows", "Avg score", "Risk observed rows", "Security flags"],
            combo_rows,
        ),
        "",
        "## Risk-Layer Public Surface",
        "",
        "Risk fields are observation-oriented. They indicate that public feeds observed URLs, hosts, or subdomains under the registered domain; they are not domain-owner verdicts.",
        "",
        markdown_table(
            ["Risk sources count", "Rows", "Share", "Mean score", "Scored rows"],
            risk_distribution_rows,
        ),
        "",
        markdown_table(
            ["Coverage tier", "Rows", "Any risk rows", "Any risk share", "Security flags", "Security flag share"],
            risk_by_coverage_rows,
        ),
        "",
        "### Threat Type Counts",
        "",
        markdown_table(["Threat type", "Rows", "Share of risk-observed rows"], threat_rows),
        "",
        "### Highest-Scoring Security-Flagged Rows",
        "",
        markdown_table(
            ["Domain", "Score", "Tier", "Sources", "Risk sources", "Threat types"],
            risky_top_rows,
        ),
        "",
        "## Rightmost DNS Label Distribution",
        "",
        "This is a simple rightmost-label view, not a Public Suffix List grouping.",
        "",
        markdown_table(
            ["Rightmost label", "Rows", "Share", "Scored rows", "Security flags"],
            rightmost_label_rows(current),
        ),
        "",
    ]

    if previous is not None and previous_meta is not None:
        previous_public_columns = previous_meta.get("public_columns", [])
        if previous_public_columns != public_columns:
            report.extend(
                [
                    "## Schema Change Since Previous Snapshot",
                    "",
                    "The previous snapshot public column list differs from the current one.",
                    "",
                    markdown_table(
                        ["Snapshot", "Public columns"],
                        [
                            [str(previous_meta.get("snapshot_date", "")), ", ".join(previous_public_columns)],
                            [str(current_meta.get("snapshot_date", "")), ", ".join(public_columns)],
                        ],
                    ),
                    "",
                ]
            )
        report.extend(release_compare_section(current, previous, previous_meta))
    elif previous_csv is not None:
        report.extend(["## Week-Over-Week Public Archive Change", "", f"Previous CSV not loaded: `{previous_csv}`.", ""])

    report.extend(
        [
            "## How To Reproduce",
            "",
            "Download the release assets and run the analyzer against the local copies:",
            "",
            "```bash",
            "mkdir -p /tmp/cdr-release-latest /tmp/cdr-release-prev",
            "",
            "gh release download data-latest \\",
            "  --repo ivan-aleshin/composite-domain-rating \\",
            "  --pattern 'domain_consensus_latest.csv.gz' \\",
            "  --pattern 'meta_latest.json' \\",
            "  --dir /tmp/cdr-release-latest",
            "",
            "gh release download data-2026-W21 \\",
            "  --repo ivan-aleshin/composite-domain-rating \\",
            "  --pattern 'domain_consensus_2026-05-24.csv.gz' \\",
            "  --pattern 'meta_2026-05-24.json' \\",
            "  --dir /tmp/cdr-release-prev",
            "",
            "python scripts/analysis/analyze_public_release.py \\",
            "  --current-csv /tmp/cdr-release-latest/domain_consensus_latest.csv.gz \\",
            "  --current-meta /tmp/cdr-release-latest/meta_latest.json \\",
            "  --current-label data-latest/domain_consensus_latest.csv.gz \\",
            "  --current-meta-label data-latest/meta_latest.json \\",
            "  --current-url https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-latest/domain_consensus_latest.csv.gz \\",
            "  --current-meta-url https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-latest/meta_latest.json \\",
            "  --previous-csv /tmp/cdr-release-prev/domain_consensus_2026-05-24.csv.gz \\",
            "  --previous-meta /tmp/cdr-release-prev/meta_2026-05-24.json \\",
            "  --previous-label data-2026-W21/domain_consensus_2026-05-24.csv.gz \\",
            "  --previous-meta-label data-2026-W21/meta_2026-05-24.json \\",
            "  --previous-url https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-2026-W21/domain_consensus_2026-05-24.csv.gz \\",
            "  --previous-meta-url https://github.com/ivan-aleshin/composite-domain-rating/releases/download/data-2026-W21/meta_2026-05-24.json \\",
            "  --output docs/analysis/releases/2026-05-31.md",
            "```",
            "",
            "## Interpretation Notes",
            "",
            "- Sparse public rows have two ranking sources and a `NULL` score by design; one-source-only rows remain internal and are excluded from public archives.",
            "- Source-combination and co-occurrence rates are measured inside the public archive, not inside the full internal mart.",
            "- Risk-source counts are independent from ranking-source counts and do not affect `consensus_score`.",
            "- Source correlation, score-without-source jackknife, and raw signal direction checks require the internal BigQuery mart or dbt analysis outputs.",
            "",
        ]
    )

    return "\n".join(report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-csv", type=Path, required=True, help="Current public CSV .gz asset")
    parser.add_argument("--current-meta", type=Path, required=True, help="Current release metadata JSON asset")
    parser.add_argument("--previous-csv", type=Path, help="Previous public CSV .gz asset for delta analysis")
    parser.add_argument("--previous-meta", type=Path, help="Previous release metadata JSON asset for delta analysis")
    parser.add_argument("--current-label", help="Human-readable label for the current CSV asset")
    parser.add_argument("--previous-label", help="Human-readable label for the previous CSV asset")
    parser.add_argument("--current-meta-label", help="Human-readable label for the current metadata asset")
    parser.add_argument("--previous-meta-label", help="Human-readable label for the previous metadata asset")
    parser.add_argument("--current-url", help="Canonical URL for the current CSV asset")
    parser.add_argument("--current-meta-url", help="Canonical URL for the current metadata asset")
    parser.add_argument("--previous-url", help="Canonical URL for the previous CSV asset")
    parser.add_argument("--previous-meta-url", help="Canonical URL for the previous metadata asset")
    parser.add_argument("--output", type=Path, required=True, help="Markdown report output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current_meta = load_meta(args.current_meta)
    current = load_public_csv(args.current_csv)

    previous = None
    previous_meta = None
    if args.previous_csv and args.previous_meta:
        previous = load_public_csv(args.previous_csv)
        previous_meta = load_meta(args.previous_meta)

    report = build_report(
        current=current,
        current_meta=current_meta,
        previous=previous,
        previous_meta=previous_meta,
        current_csv=args.current_csv,
        previous_csv=args.previous_csv,
        current_label=args.current_label,
        previous_label=args.previous_label,
        current_meta_label=args.current_meta_label,
        previous_meta_label=args.previous_meta_label,
        current_url=args.current_url,
        current_meta_url=args.current_meta_url,
        previous_url=args.previous_url,
        previous_meta_url=args.previous_meta_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
