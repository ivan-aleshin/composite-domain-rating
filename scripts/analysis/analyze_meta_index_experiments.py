"""Compare temporal domain-score indexes with walk-forward evaluation."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from reputation_index import (  # noqa: E402
    MOMENTUM_SLOPE_CLIP,
    RANK_BAND_CAPS,
    RANK_BAND_LIMITS,
    confirmed_slope,
    ordinal_ranks,
    rank_bands,
    rolling_components as calculate_rolling_components,
    scale_aware_adjustment as calculate_scale_aware_adjustment,
)


TOP_N_VALUES = (100, 1_000, 10_000, 100_000)
CANDIDATE_NAMES = (
    "sma",
    "ewma",
    "trend",
    "trend-resid-0.5",
    "trend-resid-1.0",
    "trend-momentum-0.5",
    "trend-resid-0.5-momentum-0.5",
    "ewma-confirmed-0.1",
    "ewma-confirmed-0.25",
    "ewma-scale-aware",
    "ewma-scale-aware-asymmetric",
)


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    snapshot_date: str
    csv_path: Path


def discover_assets(release_root: Path) -> list[ReleaseAsset]:
    assets = []
    for release_dir in sorted(path for path in release_root.iterdir() if path.is_dir()):
        csv_files = sorted(release_dir.glob("domain_consensus_*.csv.gz"))
        meta_files = sorted(release_dir.glob("meta_*.json"))
        if len(csv_files) != 1 or len(meta_files) != 1:
            raise RuntimeError(
                f"Expected one CSV and one metadata file under {release_dir}, "
                f"found {len(csv_files)} CSV files and {len(meta_files)} metadata files",
            )
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        assets.append(
            ReleaseAsset(
                tag=release_dir.name,
                snapshot_date=str(meta["snapshot_date"]),
                csv_path=csv_files[0],
            )
        )
    if not assets:
        raise RuntimeError(f"No release assets found under {release_root}")
    return sorted(assets, key=lambda asset: asset.snapshot_date)


def load_score_matrix(assets: list[ReleaseAsset]) -> pd.DataFrame:
    series = []
    for asset in assets:
        print(f"Loading {asset.tag} ({asset.snapshot_date})", flush=True)
        frame = pd.read_csv(
            asset.csv_path,
            usecols=["registered_domain", "consensus_score"],
            dtype={"registered_domain": "string", "consensus_score": "float32"},
        ).dropna(subset=["consensus_score"])
        series.append(
            frame.set_index("registered_domain")["consensus_score"].rename(asset.snapshot_date)
        )
    return pd.concat(series, axis=1, join="outer", copy=False)


def top_indices(scores: np.ndarray, n: int) -> set[int]:
    finite = np.flatnonzero(np.isfinite(scores))
    if len(finite) <= n:
        return set(finite)
    selected = np.argpartition(scores[finite], -n)[-n:]
    return set(finite[selected])


def rolling_components(
    values: np.ndarray,
    current_index: int,
    window: int,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    current_rows = np.flatnonzero(np.isfinite(values[:, current_index]))
    block = values[current_rows, current_index - window + 1 : current_index + 1]
    return current_rows, calculate_rolling_components(block, window)


def full_scores(
    row_count: int,
    current_rows: np.ndarray,
    eligible: np.ndarray,
    scores: np.ndarray,
) -> np.ndarray:
    result = np.full(row_count, np.nan, dtype=np.float32)
    result[current_rows[eligible]] = scores[eligible].astype(np.float32, copy=False)
    return result


def scale_aware_adjustment(
    slope: np.ndarray,
    ranks: np.ndarray,
    eligible: np.ndarray,
    *,
    asymmetric: bool,
) -> np.ndarray:
    adjustment = calculate_scale_aware_adjustment(
        slope,
        rank_bands(ranks),
        eligible,
    )
    if asymmetric:
        adjustment = np.where(adjustment < 0, adjustment * 1.5, adjustment)
    return adjustment


def candidate_arrays(
    components: dict[str, np.ndarray],
    current_scores: np.ndarray,
) -> dict[str, np.ndarray]:
    fitted = components["fitted_current"]
    slope = np.clip(components["slope"], -MOMENTUM_SLOPE_CLIP, MOMENTUM_SLOPE_CLIP)
    residual = components["residual_std"]
    momentum = 0.5 * slope
    confirmed = confirmed_slope(components)
    ranks = ordinal_ranks(current_scores)
    scale_aware = scale_aware_adjustment(
        confirmed,
        ranks,
        components["eligible"],
        asymmetric=False,
    )
    scale_aware_asymmetric = scale_aware_adjustment(
        confirmed,
        ranks,
        components["eligible"],
        asymmetric=True,
    )
    return {
        "sma": components["mean"],
        "ewma": components["ewma"],
        "trend": fitted,
        "trend-resid-0.5": fitted - 0.5 * residual,
        "trend-resid-1.0": fitted - residual,
        "trend-momentum-0.5": fitted + momentum,
        "trend-resid-0.5-momentum-0.5": fitted - 0.5 * residual + momentum,
        "ewma-confirmed-0.1": components["ewma"] + 0.1 * confirmed,
        "ewma-confirmed-0.25": components["ewma"] + 0.25 * confirmed,
        "ewma-scale-aware": components["ewma"] + scale_aware,
        "ewma-scale-aware-asymmetric": components["ewma"] + scale_aware_asymmetric,
    }


def new_state() -> dict[str, object]:
    return {
        "previous_scores": None,
        "previous_tops": None,
        "retention_100": [],
        "retention_1k": [],
        "retention_100k": [],
        "drift_p95": [],
        "forecast_100": [],
        "forecast_1k": [],
        "forecast_10k": [],
        "forecast_100k": [],
        "latest_100": math.nan,
        "latest_1k": math.nan,
        "latest_10k": math.nan,
        "latest_100k": math.nan,
        "latest_eligible": 0,
    }


def observe_candidate(
    state: dict[str, object],
    scores: np.ndarray,
    current_index: int,
    latest_index: int,
    raw_tops: dict[int, dict[int, set[int]]],
) -> None:
    tops = {n: top_indices(scores, n) for n in TOP_N_VALUES}
    previous_scores = state["previous_scores"]
    previous_tops = state["previous_tops"]
    if isinstance(previous_scores, np.ndarray) and isinstance(previous_tops, dict):
        state["retention_100"].append(len(tops[100] & previous_tops[100]) / 100)
        state["retention_1k"].append(len(tops[1_000] & previous_tops[1_000]) / 1_000)
        state["retention_100k"].append(
            len(tops[100_000] & previous_tops[100_000]) / 100_000
        )
        common = np.isfinite(scores) & np.isfinite(previous_scores)
        state["drift_p95"].append(
            float(np.quantile(np.abs(scores[common] - previous_scores[common]), 0.95))
        )
    if current_index < latest_index:
        state["forecast_100"].append(
            len(tops[100] & raw_tops[current_index + 1][100]) / 100
        )
        state["forecast_1k"].append(
            len(tops[1_000] & raw_tops[current_index + 1][1_000]) / 1_000
        )
        state["forecast_10k"].append(
            len(tops[10_000] & raw_tops[current_index + 1][10_000]) / 10_000
        )
        state["forecast_100k"].append(
            len(tops[100_000] & raw_tops[current_index + 1][100_000]) / 100_000
        )
    if current_index == latest_index:
        state["latest_100"] = len(tops[100] & raw_tops[current_index][100]) / 100
        state["latest_1k"] = len(tops[1_000] & raw_tops[current_index][1_000]) / 1_000
        state["latest_10k"] = len(tops[10_000] & raw_tops[current_index][10_000]) / 10_000
        state["latest_100k"] = (
            len(tops[100_000] & raw_tops[current_index][100_000]) / 100_000
        )
        state["latest_eligible"] = int(np.isfinite(scores).sum())
    state["previous_scores"] = scores
    state["previous_tops"] = tops


def evaluate(values: np.ndarray, windows: tuple[int, ...]) -> list[dict[str, object]]:
    latest_index = values.shape[1] - 1
    evaluation_start = max(windows) - 1
    if latest_index - evaluation_start < 1:
        raise RuntimeError(
            f"At least {max(windows) + 1} snapshots are required for walk-forward evaluation"
        )

    raw_tops = {
        index: {n: top_indices(values[:, index], n) for n in TOP_N_VALUES}
        for index in range(evaluation_start, latest_index + 1)
    }
    states = {"raw": new_state()}
    for window in windows:
        for candidate in CANDIDATE_NAMES:
            states[f"{candidate}-w{window}"] = new_state()

    for current_index in range(evaluation_start, latest_index + 1):
        print(f"Evaluating snapshot {current_index + 1}/{values.shape[1]}", flush=True)
        observe_candidate(
            states["raw"],
            values[:, current_index],
            current_index,
            latest_index,
            raw_tops,
        )
        for window in windows:
            current_rows, components = rolling_components(values, current_index, window)
            current_scores = values[current_rows, current_index]
            for candidate, local_scores in candidate_arrays(components, current_scores).items():
                scores = full_scores(
                    len(values),
                    current_rows,
                    components["eligible"],
                    local_scores,
                )
                observe_candidate(
                    states[f"{candidate}-w{window}"],
                    scores,
                    current_index,
                    latest_index,
                    raw_tops,
                )

    rows = []
    for name, state in states.items():
        rows.append(
            {
                "candidate": name,
                "latest_eligible": state["latest_eligible"],
                "latest_100": state["latest_100"],
                "latest_1k": state["latest_1k"],
                "latest_10k": state["latest_10k"],
                "latest_100k": state["latest_100k"],
                "retention_100": float(np.median(state["retention_100"])),
                "retention_1k": float(np.median(state["retention_1k"])),
                "retention_100k": float(np.median(state["retention_100k"])),
                "drift_p95": float(np.median(state["drift_p95"])),
                "forecast_100": float(np.median(state["forecast_100"])),
                "forecast_1k": float(np.median(state["forecast_1k"])),
                "forecast_10k": float(np.median(state["forecast_10k"])),
                "forecast_100k": float(np.median(state["forecast_100k"])),
            }
        )
    return rows


def pct(value: object) -> str:
    return f"{float(value):.2%}"


def build_report(
    assets: list[ReleaseAsset],
    windows: tuple[int, ...],
    rows: list[dict[str, object]],
) -> str:
    evaluation_start = max(windows) - 1
    evaluation_dates = assets[evaluation_start:]
    lines = [
        f"# Temporal Meta-index Experiment - {assets[-1].snapshot_date}",
        "",
        f"Generated at `{datetime.now(timezone.utc).isoformat()}`.",
        f"Input window: `{assets[0].snapshot_date}` to `{assets[-1].snapshot_date}` "
        f"({len(assets)} snapshots).",
        f"Common evaluation window: `{evaluation_dates[0].snapshot_date}` to "
        f"`{evaluation_dates[-1].snapshot_date}` ({len(evaluation_dates) - 1} transitions).",
        "",
        "## Method",
        "",
        "All candidates use only data available at the evaluated snapshot. Forecast overlap compares "
        "a candidate ranking at week t with the raw consensus ranking at week t+1.",
        "Domains must be scored in the current snapshot and in at least 75% of a candidate's history "
        "window. Raw consensus has no history requirement.",
        "",
        "- `sma`: simple rolling mean.",
        "- `ewma`: exponentially weighted mean with a half-life equal to half the window.",
        "- `trend`: fitted current level from an OLS line over the window.",
        "- `resid`: penalty based on residual standard deviation around the fitted trend.",
        "- `momentum-0.5`: adds half a week of fitted slope, clipped to +/-3 score points per week.",
        "- `confirmed`: removes 1.5 slope standard errors before applying a momentum adjustment.",
        "- `scale-aware`: applies a saturating adjustment capped by current raw rank band at "
        "0.01/0.03/0.10/0.30/0.75 score points.",
        "- `asymmetric`: makes confirmed downward adjustments 1.5 times stronger than upward ones.",
        "",
        "## Results",
        "",
        "| Candidate | Latest eligible | Latest top-1k vs raw | Indexed top-1k retention | "
        "Median p95 index drift | Next-week top-1k overlap | Next-week top-10k overlap |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['candidate']} | {int(row['latest_eligible']):,} | {pct(row['latest_1k'])} | "
            f"{pct(row['retention_1k'])} | {float(row['drift_p95']):.4f} | "
            f"{pct(row['forecast_1k'])} | {pct(row['forecast_10k'])} |"
        )
    lines.extend(
        [
            "",
            "## Top-100 Results",
            "",
            "| Candidate | Latest top-100 vs raw | Indexed top-100 retention | "
            "Next-week top-100 overlap |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['candidate']} | {pct(row['latest_100'])} | "
            f"{pct(row['retention_100'])} | {pct(row['forecast_100'])} |"
        )
    lines.extend(
        [
            "",
            "## Broader-rank Results",
            "",
            "| Candidate | Latest top-10k vs raw | Latest top-100k vs raw | "
            "Indexed top-100k retention | Next-week top-100k overlap |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['candidate']} | {pct(row['latest_10k'])} | "
            f"{pct(row['latest_100k'])} | {pct(row['retention_100k'])} | "
            f"{pct(row['forecast_100k'])} |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- The common evaluation interval contains only five week-to-week transitions.",
            "- Forecast overlap measures agreement with next week's consensus, not external ground truth.",
            "- The experiment uses public consensus scores; source-level movement is not available here.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windows", type=int, nargs="+", default=(4, 8))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    windows = tuple(sorted(set(args.windows)))
    if not windows or min(windows) < 3:
        raise RuntimeError("Windows must contain integers greater than or equal to 3")
    assets = discover_assets(args.release_root)
    scores = load_score_matrix(assets)
    rows = evaluate(scores.to_numpy(dtype=np.float32, copy=False), windows)
    report = build_report(assets, windows, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
