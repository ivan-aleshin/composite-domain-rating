"""Evaluate aggregate-only DDRI combination candidates walk-forward."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_ddri_components import source_mask
from analyze_meta_index_experiments import (
    RANK_BAND_CAPS,
    RANK_BAND_LIMITS,
    confirmed_slope,
    discover_assets,
    full_scores,
    ordinal_ranks,
    rolling_components,
    scale_aware_adjustment,
    top_indices,
)


TOP_N_VALUES = (100, 1_000, 10_000, 100_000)
CANDIDATE_NAMES = (
    "level",
    "trend-gated",
    "shock-gated",
    "shock-damped-0.25",
    "shock-damped-0.5",
    "conservative-0.5",
    "conservative-1.0",
)


def load_history(assets: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    score_series = []
    count_series = []
    mask_series = []
    for asset in assets:
        print(f"Loading {asset.tag} ({asset.snapshot_date})", flush=True)
        frame = pd.read_csv(
            asset.csv_path,
            usecols=[
                "registered_domain",
                "consensus_score",
                "sources_count",
                "ranking_sources_present",
            ],
            dtype={
                "registered_domain": "string",
                "consensus_score": "float32",
                "sources_count": "uint8",
                "ranking_sources_present": "string",
            },
        ).dropna(subset=["consensus_score"])
        frame = frame.set_index("registered_domain")
        score_series.append(frame["consensus_score"].rename(asset.snapshot_date))
        count_series.append(frame["sources_count"].rename(asset.snapshot_date))
        mask_series.append(source_mask(frame["ranking_sources_present"]).rename(asset.snapshot_date))

    scores = pd.concat(score_series, axis=1, join="outer", copy=False)
    counts = pd.concat(count_series, axis=1, join="outer", copy=False).reindex(scores.index)
    masks = pd.concat(mask_series, axis=1, join="outer", copy=False).reindex(scores.index)
    return (
        scores.to_numpy(dtype=np.float32, copy=False),
        counts.fillna(0).to_numpy(dtype=np.uint8),
        masks.fillna(0).to_numpy(dtype=np.uint8),
    )


def local_ewma(block: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    observed = np.isfinite(block)
    weights = np.power(0.5, (block.shape[1] - 1 - np.arange(block.shape[1])) / 2)
    denominator = (observed * weights).sum(axis=1)
    numerator = (np.where(observed, block, 0) * weights).sum(axis=1)
    values = np.divide(
        numerator,
        denominator,
        out=np.zeros(len(block), dtype=np.float64),
        where=denominator > 0,
    )
    return values, observed.sum(axis=1) >= 3


def confidence_and_candidates(
    scores: np.ndarray,
    source_counts: np.ndarray,
    source_masks: np.ndarray,
    current_index: int,
) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray, np.ndarray]:
    current_rows = np.flatnonzero(np.isfinite(scores[:, current_index]))
    current_scores = scores[current_rows, current_index]
    ranks = ordinal_ranks(current_scores)
    bands = np.searchsorted(RANK_BAND_LIMITS, ranks, side="left")

    rows4, level = rolling_components(scores, current_index, 4)
    rows8, trend = rolling_components(scores, current_index, 8)
    if not np.array_equal(current_rows, rows4) or not np.array_equal(current_rows, rows8):
        raise RuntimeError("Current-domain alignment failed")

    reputation = np.where(level["eligible"], level["ewma"], current_scores)
    confirmed = confirmed_slope(trend)
    trend_adjustment = scale_aware_adjustment(
        confirmed,
        ranks,
        trend["eligible"],
        asymmetric=False,
    )

    score_window = scores[current_rows, current_index - 7 : current_index + 1]
    count_window = source_counts[current_rows, current_index - 7 : current_index + 1]
    mask_window = source_masks[current_rows, current_index - 7 : current_index + 1]
    observed = np.isfinite(score_window)
    history_confidence = observed.sum(axis=1) / 8
    coverage_confidence = np.divide(
        np.where(observed, count_window, 0).sum(axis=1),
        observed.sum(axis=1) * 5,
        out=np.zeros(len(current_rows), dtype=np.float64),
        where=observed.sum(axis=1) > 0,
    )
    comparable = observed[:, :-1] & observed[:, 1:]
    composition_confidence = np.divide(
        (comparable & (mask_window[:, :-1] == mask_window[:, 1:])).sum(axis=1),
        comparable.sum(axis=1),
        out=np.zeros(len(current_rows), dtype=np.float64),
        where=comparable.sum(axis=1) > 0,
    )

    noise_confidence = np.zeros(len(current_rows), dtype=np.float64)
    for band_index in range(len(RANK_BAND_CAPS)):
        mask = trend["eligible"] & (bands == band_index)
        if not mask.any():
            continue
        scale = max(float(np.median(trend["residual_std"][mask])), 0.05)
        noise_confidence[mask] = 1 / (1 + trend["residual_std"][mask] / scale)

    confidence = np.prod(
        np.column_stack(
            (
                history_confidence,
                coverage_confidence,
                composition_confidence,
                noise_confidence,
            )
        ),
        axis=1,
    ) ** 0.25

    previous_block = scores[current_rows, current_index - 4 : current_index]
    previous_level, previous_level_eligible = local_ewma(previous_block)
    current_structural_shock = (
        np.isfinite(scores[current_rows, current_index - 1])
        & (source_masks[current_rows, current_index - 1] != source_masks[current_rows, current_index])
    )
    held_level = np.where(
        current_structural_shock & previous_level_eligible,
        previous_level,
        reputation,
    )
    damped_level_025 = np.where(
        current_structural_shock & previous_level_eligible,
        previous_level + 0.25 * (reputation - previous_level),
        reputation,
    )
    damped_level_05 = np.where(
        current_structural_shock & previous_level_eligible,
        previous_level + 0.5 * (reputation - previous_level),
        reputation,
    )
    gated_adjustment = confidence * trend_adjustment
    shock_gated_adjustment = np.where(current_structural_shock, 0, gated_adjustment)
    uncertainty_penalty = RANK_BAND_CAPS[bands] * (1 - confidence)

    candidates = {
        "level": reputation,
        "trend-gated": reputation + gated_adjustment,
        "shock-gated": held_level + shock_gated_adjustment,
        "shock-damped-0.25": damped_level_025 + shock_gated_adjustment,
        "shock-damped-0.5": damped_level_05 + shock_gated_adjustment,
        "conservative-0.5": held_level + shock_gated_adjustment - 0.5 * uncertainty_penalty,
        "conservative-1.0": held_level + shock_gated_adjustment - uncertainty_penalty,
    }
    return current_rows, candidates, current_structural_shock, confidence


def new_state() -> dict[str, object]:
    return {
        "previous": None,
        "previous_tops": None,
        "retention": {n: [] for n in TOP_N_VALUES},
        "forecast": {n: [] for n in TOP_N_VALUES},
        "latest": {n: np.nan for n in TOP_N_VALUES},
        "drift_p95": [],
        "shock_median": [],
        "shock_p95": [],
        "latest_eligible": 0,
    }


def observe(
    state: dict[str, object],
    values: np.ndarray,
    shock: np.ndarray,
    current_index: int,
    latest_index: int,
    raw_tops: dict[int, dict[int, set[int]]],
) -> None:
    tops = {n: top_indices(values, n) for n in TOP_N_VALUES}
    previous = state["previous"]
    previous_tops = state["previous_tops"]
    if isinstance(previous, np.ndarray) and isinstance(previous_tops, dict):
        for n in TOP_N_VALUES:
            state["retention"][n].append(len(tops[n] & previous_tops[n]) / n)
        common = np.isfinite(values) & np.isfinite(previous)
        delta = np.abs(values[common] - previous[common])
        state["drift_p95"].append(float(np.quantile(delta, 0.95)))
        shock_common = common & shock
        if shock_common.any():
            shock_delta = np.abs(values[shock_common] - previous[shock_common])
            state["shock_median"].append(float(np.median(shock_delta)))
            state["shock_p95"].append(float(np.quantile(shock_delta, 0.95)))
    if current_index < latest_index:
        for n in TOP_N_VALUES:
            state["forecast"][n].append(
                len(tops[n] & raw_tops[current_index + 1][n]) / n
            )
    if current_index == latest_index:
        for n in TOP_N_VALUES:
            state["latest"][n] = len(tops[n] & raw_tops[current_index][n]) / n
        state["latest_eligible"] = int(np.isfinite(values).sum())
    state["previous"] = values
    state["previous_tops"] = tops


def evaluate(
    scores: np.ndarray,
    source_counts: np.ndarray,
    source_masks: np.ndarray,
) -> list[dict[str, object]]:
    latest_index = scores.shape[1] - 1
    evaluation_start = 7
    if latest_index <= evaluation_start:
        raise RuntimeError("At least nine snapshots are required for walk-forward evaluation")
    raw_tops = {
        index: {n: top_indices(scores[:, index], n) for n in TOP_N_VALUES}
        for index in range(evaluation_start, latest_index + 1)
    }
    states = {"raw": new_state(), **{name: new_state() for name in CANDIDATE_NAMES}}

    for current_index in range(evaluation_start, latest_index + 1):
        print(f"Evaluating snapshot {current_index + 1}/{scores.shape[1]}", flush=True)
        current_rows, candidates, local_shock, _ = confidence_and_candidates(
            scores,
            source_counts,
            source_masks,
            current_index,
        )
        shock = np.zeros(len(scores), dtype=bool)
        shock[current_rows] = local_shock
        observe(
            states["raw"],
            scores[:, current_index],
            shock,
            current_index,
            latest_index,
            raw_tops,
        )
        for name, local_values in candidates.items():
            values = full_scores(
                len(scores),
                current_rows,
                np.ones(len(current_rows), dtype=bool),
                local_values,
            )
            observe(states[name], values, shock, current_index, latest_index, raw_tops)

    rows = []
    for name, state in states.items():
        rows.append(
            {
                "candidate": name,
                "eligible": state["latest_eligible"],
                "latest": state["latest"],
                "retention": {n: float(np.median(state["retention"][n])) for n in TOP_N_VALUES},
                "forecast": {n: float(np.median(state["forecast"][n])) for n in TOP_N_VALUES},
                "drift_p95": float(np.median(state["drift_p95"])),
                "shock_median": float(np.median(state["shock_median"])),
                "shock_p95": float(np.median(state["shock_p95"])),
            }
        )
    return rows


def pct(value: float) -> str:
    return f"{value:.2%}"


def build_report(assets: list, rows: list[dict[str, object]]) -> str:
    lines = [
        f"# Aggregate-only DDRI Candidate Evaluation - {assets[-1].snapshot_date}",
        "",
        f"Generated at `{datetime.now(timezone.utc).isoformat()}` from {len(assets)} public snapshots.",
        "Common W8 walk-forward evaluation uses five transitions from `2026-07-26` through `2026-08-30`.",
        "Risk remains a separate observation layer and is not included in candidate scores.",
        "",
        "## Formulas",
        "",
        "- `level`: W4 EWMA reputation level.",
        "- `trend-gated`: level plus confidence times the scale-aware W8 trend adjustment.",
        "- `shock-gated`: holds the previous W4 EWMA and suppresses trend for one week when "
        "`ranking_sources_present` changes.",
        "- `shock-damped-k`: accepts k of the new W4 EWMA on a structural-shock week and suppresses trend.",
        "- `conservative-k`: shock-gated minus k times rank-band cap times `(1 - confidence)`.",
        "",
        "## Main Results",
        "",
        "| Candidate | Latest top-100 | Latest top-1k | Top-1k retention | P95 drift | "
        "Next top-1k | Latest top-100k | Top-100k retention | Next top-100k |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['candidate']} | {pct(row['latest'][100])} | {pct(row['latest'][1_000])} | "
            f"{pct(row['retention'][1_000])} | {row['drift_p95']:.4f} | "
            f"{pct(row['forecast'][1_000])} | {pct(row['latest'][100_000])} | "
            f"{pct(row['retention'][100_000])} | {pct(row['forecast'][100_000])} |"
        )
    lines.extend(
        [
            "",
            "## Structural-shock Response",
            "",
            "Median metrics below are computed only for domains whose published source set changed "
            "between adjacent snapshots.",
            "",
            "| Candidate | Median absolute index change | P95 absolute index change |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['candidate']} | {row['shock_median']:.4f} | {row['shock_p95']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Confidence and penalties are exploratory heuristics, not calibrated probabilities.",
            "- Next-week overlap is a lag diagnostic, not the optimization target.",
            "- Five walk-forward transitions are enough to reject unstable formulas, not select final coefficients.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assets = discover_assets(args.release_root)
    scores, source_counts, source_masks = load_history(assets)
    rows = evaluate(scores, source_counts, source_masks)
    report = build_report(assets, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
