"""Build and summarize aggregate-only DDRI components from public releases."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_meta_index_experiments import (
    RANK_BAND_CAPS,
    RANK_BAND_LIMITS,
    confirmed_slope,
    discover_assets,
    ordinal_ranks,
    rolling_components,
    scale_aware_adjustment,
)


HISTORY_COLUMNS = (
    "registered_domain",
    "consensus_score",
    "sources_count",
    "ranking_sources_present",
    "risk_sources_count",
)
LATEST_COLUMNS = HISTORY_COLUMNS + (
    "threat_types",
    "snapshot_date",
)
SOURCE_BITS = {
    "tranco": 1,
    "majestic": 2,
    "radar": 4,
    "crux": 8,
    "opr": 16,
}
RANK_BAND_LABELS = ("top-100", "top-1k", "top-10k", "top-100k", "below-100k")


def source_mask(values: pd.Series) -> pd.Series:
    combinations = values.fillna("").unique()
    mapping = {
        combination: sum(
            bit for source, bit in SOURCE_BITS.items() if source in combination.split(",")
        )
        for combination in combinations
    }
    return values.fillna("").map(mapping).astype("uint8")


def load_component_history(
    assets: list,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    latest = pd.read_csv(
        assets[-1].csv_path,
        usecols=LATEST_COLUMNS,
        dtype={
            "registered_domain": "string",
            "consensus_score": "float32",
            "sources_count": "uint8",
            "ranking_sources_present": "string",
            "risk_sources_count": "uint8",
            "threat_types": "string",
            "snapshot_date": "string",
        },
    ).dropna(subset=["consensus_score"])
    latest = latest.set_index("registered_domain", drop=False)
    domains = latest.index

    score_columns = []
    source_count_columns = []
    source_mask_columns = []
    risk_count_columns = []
    for asset in assets:
        print(f"Loading {asset.tag} ({asset.snapshot_date})", flush=True)
        frame = pd.read_csv(
            asset.csv_path,
            usecols=HISTORY_COLUMNS,
            dtype={
                "registered_domain": "string",
                "consensus_score": "float32",
                "sources_count": "uint8",
                "ranking_sources_present": "string",
                "risk_sources_count": "uint8",
            },
        ).set_index("registered_domain")
        aligned = frame.reindex(domains)
        score_columns.append(aligned["consensus_score"].to_numpy(dtype=np.float32))
        source_count_columns.append(
            aligned["sources_count"].fillna(0).to_numpy(dtype=np.uint8)
        )
        source_mask_columns.append(
            source_mask(aligned["ranking_sources_present"]).to_numpy(dtype=np.uint8)
        )
        risk_count_columns.append(
            aligned["risk_sources_count"].fillna(0).to_numpy(dtype=np.uint8)
        )

    return (
        latest,
        np.column_stack(score_columns),
        np.column_stack(source_count_columns),
        np.column_stack(source_mask_columns),
        np.column_stack(risk_count_columns),
    )


def rank_band(ranks: np.ndarray) -> np.ndarray:
    return np.searchsorted(RANK_BAND_LIMITS, ranks, side="left")


def band_noise_confidence(
    residual_std: np.ndarray,
    bands: np.ndarray,
    eligible: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    confidence = np.zeros_like(residual_std)
    scales = np.zeros(len(RANK_BAND_LABELS), dtype=np.float64)
    for band_index in range(len(RANK_BAND_LABELS)):
        mask = eligible & (bands == band_index)
        if not mask.any():
            continue
        scale = max(float(np.median(residual_std[mask])), 0.05)
        scales[band_index] = scale
        confidence[mask] = 1 / (1 + residual_std[mask] / scale)
    return confidence, scales


def source_set_confidence(
    scores: np.ndarray,
    masks: np.ndarray,
    window: int,
) -> np.ndarray:
    score_window = scores[:, -window:]
    mask_window = masks[:, -window:]
    comparable = np.isfinite(score_window[:, :-1]) & np.isfinite(score_window[:, 1:])
    unchanged = comparable & (mask_window[:, :-1] == mask_window[:, 1:])
    comparable_count = comparable.sum(axis=1)
    return np.divide(
        unchanged.sum(axis=1),
        comparable_count,
        out=np.zeros(len(scores), dtype=np.float64),
        where=comparable_count > 0,
    )


def risk_states(current: np.ndarray, recent: np.ndarray) -> np.ndarray:
    return np.select(
        (
            current >= 2,
            current == 1,
            (current == 0) & (recent > 0),
        ),
        (
            "multi-source-observed",
            "single-source-observed",
            "recent-history-only",
        ),
        default="none-observed",
    )


def build_components(
    latest: pd.DataFrame,
    scores: np.ndarray,
    source_counts: np.ndarray,
    source_masks: np.ndarray,
    risk_counts: np.ndarray,
) -> tuple[pd.DataFrame, np.ndarray]:
    latest_index = scores.shape[1] - 1
    latest_scores = scores[:, latest_index]
    ranks = ordinal_ranks(latest_scores)
    bands = rank_band(ranks)

    rows4, level = rolling_components(scores, latest_index, 4)
    rows8, trend = rolling_components(scores, latest_index, 8)
    if len(rows4) != len(scores) or len(rows8) != len(scores):
        raise RuntimeError("Latest scored-domain alignment failed")

    confirmed = confirmed_slope(trend)
    trend_adjustment = scale_aware_adjustment(
        confirmed,
        ranks,
        trend["eligible"],
        asymmetric=False,
    )
    material_trend = np.abs(trend_adjustment) >= 0.1 * RANK_BAND_CAPS[bands]
    noise_confidence, noise_scales = band_noise_confidence(
        trend["residual_std"],
        bands,
        trend["eligible"],
    )

    history_window = scores[:, -8:]
    observed = np.isfinite(history_window)
    history_confidence = observed.sum(axis=1) / 8
    coverage_sum = np.where(observed, source_counts[:, -8:], 0).sum(axis=1)
    coverage_count = observed.sum(axis=1)
    coverage_confidence = np.divide(
        coverage_sum,
        coverage_count * len(SOURCE_BITS),
        out=np.zeros(len(scores), dtype=np.float64),
        where=coverage_count > 0,
    )
    composition_confidence = source_set_confidence(scores, source_masks, 8)

    confidence_terms = np.column_stack(
        (
            history_confidence,
            coverage_confidence,
            composition_confidence,
            noise_confidence,
        )
    )
    reputation_confidence = 100 * np.prod(confidence_terms, axis=1) ** 0.25

    trend_state = np.select(
        (
            trend["eligible"] & material_trend & (confirmed > 0),
            trend["eligible"] & material_trend & (confirmed < 0),
            trend["eligible"] & (noise_confidence >= 0.5),
        ),
        ("rising", "falling", "stable"),
        default="uncertain",
    )
    risk_state = risk_states(risk_counts[:, -1], risk_counts[:, -8:].max(axis=1))

    components = pd.DataFrame(
        {
            "registered_domain": latest.index.to_numpy(),
            "snapshot_date": latest["snapshot_date"].to_numpy(),
            "consensus_score": latest_scores,
            "current_rank": ranks,
            "rank_band": np.asarray(RANK_BAND_LABELS, dtype=object)[bands],
            "reputation_score": level["ewma"],
            "reputation_confidence": reputation_confidence,
            "history_confidence": 100 * history_confidence,
            "coverage_confidence": 100 * coverage_confidence,
            "source_set_confidence": 100 * composition_confidence,
            "temporal_noise_confidence": 100 * noise_confidence,
            "reputation_trend": trend_state,
            "trend_slope": trend["slope"],
            "confirmed_trend_slope": confirmed,
            "scale_aware_trend_adjustment": trend_adjustment,
            "residual_std": trend["residual_std"],
            "observed_risk": risk_state,
            "risk_sources_count": latest["risk_sources_count"].to_numpy(dtype=np.uint8),
            "threat_types": latest["threat_types"].fillna("").to_numpy(),
        }
    )
    components.loc[~level["eligible"], "reputation_score"] = np.nan
    components.loc[~trend["eligible"], "reputation_confidence"] = np.nan
    return components, noise_scales


def pct(value: float) -> str:
    return f"{value:.2%}"


def quantiles(values: pd.Series) -> str:
    result = values.quantile((0.1, 0.5, 0.9))
    return f"{result.iloc[0]:.2f} / {result.iloc[1]:.2f} / {result.iloc[2]:.2f}"


def build_report(
    assets: list,
    components: pd.DataFrame,
    noise_scales: np.ndarray,
) -> str:
    lines = [
        f"# Aggregate-only DDRI Components - {assets[-1].snapshot_date}",
        "",
        f"Generated at `{datetime.now(timezone.utc).isoformat()}` from {len(assets)} public snapshots.",
        "No source-specific ranks or percentiles are retained or published.",
        "",
        "## Component Definitions",
        "",
        "- `reputation_score`: W4 EWMA of public `consensus_score`.",
        "- `reputation_confidence`: geometric mean of W8 history completeness, mean source coverage, "
        "source-set continuity, and rank-band-normalized temporal-noise confidence.",
        "- `reputation_trend`: W8 aggregate slope after removing 1.5 slope standard errors and "
        "requiring an adjustment of at least 10% of the current rank-band cap.",
        "- `observed_risk`: current or recent published risk observations; `none-observed` does not mean safe.",
        "",
        "The confidence formula is an exploratory heuristic and is not a calibrated probability.",
        "",
        "## Overall Summary",
        "",
        f"- Latest scored domains: **{len(components):,}**.",
        f"- Reputation score p10 / p50 / p90: **{quantiles(components['reputation_score'])}**.",
        f"- Confidence p10 / p50 / p90: **{quantiles(components['reputation_confidence'])}**.",
        "",
        "## Rank-band Summary",
        "",
        "| Band | Domains | Median reputation | Median confidence | Rising | Falling | Stable | Uncertain | Source-set changed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for band_index, label in enumerate(RANK_BAND_LABELS):
        frame = components[components["rank_band"] == label]
        trend_share = frame["reputation_trend"].value_counts(normalize=True)
        changed = (frame["source_set_confidence"] < 100).mean()
        lines.append(
            f"| {label} | {len(frame):,} | {frame['reputation_score'].median():.2f} | "
            f"{frame['reputation_confidence'].median():.2f} | "
            f"{pct(trend_share.get('rising', 0))} | {pct(trend_share.get('falling', 0))} | "
            f"{pct(trend_share.get('stable', 0))} | {pct(trend_share.get('uncertain', 0))} | "
            f"{pct(changed)} |"
        )
    lines.extend(
        [
            "",
            "## Temporal-noise Scales",
            "",
            "Median W8 residual standard deviation used to normalize confidence:",
            "",
            "| Band | Residual std scale |",
            "| --- | ---: |",
        ]
    )
    for label, scale in zip(RANK_BAND_LABELS, noise_scales):
        lines.append(f"| {label} | {scale:.4f} |")
    lines.extend(
        [
            "",
            "## Risk Summary",
            "",
            "| State | Domains | Share |",
            "| --- | ---: | ---: |",
        ]
    )
    risk_counts = components["observed_risk"].value_counts()
    for state, count in risk_counts.items():
        lines.append(f"| {state} | {count:,} | {pct(count / len(components))} |")
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Component values are descriptive and have not been calibrated against external trust labels.",
            "- Source-set continuity detects composition changes but cannot identify which source caused movement.",
            "- The four components are intentionally not combined into a final DDRI score in this stage.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assets = discover_assets(args.release_root)
    if len(assets) < 8:
        raise RuntimeError("At least eight public snapshots are required")
    latest, scores, source_counts, source_masks, risk_counts = load_component_history(assets)
    components, noise_scales = build_components(
        latest,
        scores,
        source_counts,
        source_masks,
        risk_counts,
    )
    report = build_report(assets, components, noise_scales)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    if args.snapshot_output:
        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        components.to_csv(args.snapshot_output, index=False, compression="infer")
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
