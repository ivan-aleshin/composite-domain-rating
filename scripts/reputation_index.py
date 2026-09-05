"""Aggregate-only Dynamic Domain Reputation Index calculations."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DDRI_METHODOLOGY_VERSION = "ddri-v0.1.1-experimental"
HISTORY_WINDOW = 8
LEVEL_WINDOW = 4
LEVEL_HALF_LIFE_WEEKS = 2.0
MIN_HISTORY_SHARE = 0.75
SNAPSHOT_DATE_TOLERANCE_DAYS = 1
MIN_HISTORY_RELEASES = max(3, math.ceil(HISTORY_WINDOW * MIN_HISTORY_SHARE))
MIN_LEVEL_RELEASES = max(3, math.ceil(LEVEL_WINDOW * MIN_HISTORY_SHARE))
MAX_RANKING_SOURCES = 5
MOMENTUM_SLOPE_CLIP = 3.0
MOMENTUM_CONFIDENCE_KAPPA = 1.5
SHOCK_DAMPING = 0.5
RANK_BAND_LIMITS = np.array((100, 1_000, 10_000, 100_000), dtype=np.int32)
RANK_BAND_CAPS = np.array((0.01, 0.03, 0.10, 0.30, 0.75), dtype=np.float64)
RANK_BAND_LABELS = np.array(
    ("top-100", "top-1k", "top-10k", "top-100k", "below-100k"),
    dtype=object,
)
SOURCE_BITS = {
    "tranco": 1,
    "majestic": 2,
    "radar": 4,
    "crux": 8,
    "opr": 16,
}
SCORE_COLUMNS = (
    "registered_domain",
    "consensus_score",
    "sources_count",
    "ranking_sources_present",
    "risk_sources_count",
)
INPUT_COLUMNS = SCORE_COLUMNS + ("snapshot_date", "methodology_version")
PUBLIC_COLUMNS = (
    "registered_domain",
    "reputation_score",
    "reputation_confidence",
    "reputation_trend",
    "trend_strength",
    "history_observations",
    "structural_shock",
    "ddri_score_candidate",
    "observed_risk",
    "snapshot_date",
    "ddri_methodology_version",
)
DATED_CSV_PATTERN = re.compile(r"^domain_consensus_\d{4}-\d{2}-\d{2}\.csv\.gz$")
DATED_META_PATTERN = re.compile(r"^meta_\d{4}-\d{2}-\d{2}\.json$")
WEEKLY_TAG_PATTERN = re.compile(r"^data-(\d{4})-W(\d{2})$")


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    snapshot_date: str
    methodology_version: str
    csv_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class ReputationBuild:
    frame: pd.DataFrame
    noise_scales: np.ndarray
    input_assets: tuple[ReleaseAsset, ...]
    history_tags: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _single_matching_file(directory: Path, pattern: re.Pattern[str], label: str) -> Path:
    matches = sorted(path for path in directory.iterdir() if pattern.fullmatch(path.name))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one dated {label} under {directory}, found {len(matches)}"
        )
    return matches[0]


def weekly_tag_date(tag: str) -> date:
    match = WEEKLY_TAG_PATTERN.fullmatch(tag)
    if not match:
        raise RuntimeError(f"Invalid weekly release tag: {tag}")
    return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)


def weekly_tag(value: date) -> str:
    iso_year, iso_week, _ = value.isocalendar()
    return f"data-{iso_year}-W{iso_week:02d}"


def history_tags(latest_tag: str, window: int = HISTORY_WINDOW) -> tuple[str, ...]:
    latest_week = weekly_tag_date(latest_tag)
    return tuple(
        weekly_tag(latest_week - timedelta(weeks=offset))
        for offset in range(window - 1, -1, -1)
    )


def _validate_history_assets(assets: list[ReleaseAsset]) -> tuple[str, ...]:
    if not assets:
        raise RuntimeError("No public consensus snapshots found")

    assets.sort(key=lambda asset: weekly_tag_date(asset.tag))
    expected_tags = history_tags(assets[-1].tag)
    expected_tag_set = set(expected_tags)
    selected_tags = {asset.tag for asset in assets}
    if not selected_tags.issubset(expected_tag_set):
        unexpected = sorted(selected_tags - expected_tag_set)
        raise RuntimeError(f"Snapshots outside the DDRI history window: {unexpected}")

    if len(assets) < MIN_HISTORY_RELEASES:
        raise RuntimeError(
            f"At least {MIN_HISTORY_RELEASES} of the latest {HISTORY_WINDOW} weekly releases "
            f"are required, found {len(assets)}"
        )
    recent_tags = set(expected_tags[-LEVEL_WINDOW:])
    recent_count = sum(asset.tag in recent_tags for asset in assets)
    if recent_count < MIN_LEVEL_RELEASES:
        raise RuntimeError(
            f"At least {MIN_LEVEL_RELEASES} of the latest {LEVEL_WINDOW} weekly releases "
            f"are required, found {recent_count}"
        )

    parsed_dates = [date.fromisoformat(asset.snapshot_date) for asset in assets]
    if len(set(parsed_dates)) != len(parsed_dates):
        raise RuntimeError(
            "Duplicate snapshot dates in DDRI history: "
            + ", ".join(value.isoformat() for value in parsed_dates)
        )
    for asset, snapshot_date in zip(assets, parsed_dates):
        expected_tag = weekly_tag(snapshot_date)
        if asset.tag != expected_tag:
            raise RuntimeError(
                f"Snapshot {asset.snapshot_date} must use release tag {expected_tag}, "
                f"found {asset.tag}"
            )
    for previous_asset, current_asset, previous_date, current_date in zip(
        assets, assets[1:], parsed_dates, parsed_dates[1:]
    ):
        expected_days = (
            weekly_tag_date(current_asset.tag) - weekly_tag_date(previous_asset.tag)
        ).days
        actual_days = (current_date - previous_date).days
        if abs(actual_days - expected_days) > SNAPSHOT_DATE_TOLERANCE_DAYS:
            raise RuntimeError(
                "Snapshot dates must follow weekly release spacing within "
                f"+/-{SNAPSHOT_DATE_TOLERANCE_DAYS} day; found "
                f"{previous_date.isoformat()} then {current_date.isoformat()}"
            )

    versions = {asset.methodology_version for asset in assets}
    if len(versions) != 1:
        raise RuntimeError(
            "DDRI history must use one consensus methodology version; found "
            + ", ".join(sorted(versions))
        )
    return expected_tags


def discover_release_assets(release_root: Path) -> list[ReleaseAsset]:
    if not release_root.is_dir():
        raise RuntimeError(f"Release root does not exist: {release_root}")

    assets: list[ReleaseAsset] = []
    release_dirs = (
        path
        for path in release_root.iterdir()
        if path.is_dir() and re.fullmatch(r"data-\d{4}-W\d{2}", path.name)
    )
    for release_dir in sorted(release_dirs):
        csv_path = _single_matching_file(release_dir, DATED_CSV_PATTERN, "consensus CSV")
        metadata_path = _single_matching_file(release_dir, DATED_META_PATTERN, "metadata file")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        snapshot_date = str(metadata.get("snapshot_date") or "")
        methodology_version = str(metadata.get("methodology_version") or "")
        if not snapshot_date or not methodology_version:
            raise RuntimeError(f"Incomplete release metadata: {metadata_path}")
        metadata_tag = str((metadata.get("release") or {}).get("tag") or release_dir.name)
        if metadata_tag != release_dir.name:
            raise RuntimeError(
                f"Release tag mismatch for {metadata_path}: {metadata_tag} != {release_dir.name}"
            )
        expected_csv_name = f"domain_consensus_{snapshot_date}.csv.gz"
        expected_metadata_name = f"meta_{snapshot_date}.json"
        if csv_path.name != expected_csv_name or metadata_path.name != expected_metadata_name:
            raise RuntimeError(
                f"Asset date mismatch under {release_dir}: expected "
                f"{expected_csv_name} and {expected_metadata_name}"
            )
        assets.append(
            ReleaseAsset(
                tag=release_dir.name,
                snapshot_date=snapshot_date,
                methodology_version=methodology_version,
                csv_path=csv_path,
                metadata_path=metadata_path,
            )
        )

    assets.sort(key=lambda asset: weekly_tag_date(asset.tag))
    latest_tag = assets[-1].tag if assets else ""
    selected_tag_set = set(history_tags(latest_tag)) if latest_tag else set()
    selected = [asset for asset in assets if asset.tag in selected_tag_set]
    _validate_history_assets(selected)
    return selected


def source_mask(values: pd.Series) -> pd.Series:
    combinations = values.fillna("").unique()
    mapping = {
        combination: sum(
            SOURCE_BITS.get(source.strip(), 0)
            for source in combination.split(",")
            if source.strip()
        )
        for combination in combinations
    }
    return values.fillna("").map(mapping).astype("uint8")


def _validate_release_frame(frame: pd.DataFrame, asset: ReleaseAsset) -> None:
    if frame["registered_domain"].isna().any():
        raise RuntimeError(f"Null registered_domain in {asset.csv_path}")
    if frame["registered_domain"].duplicated().any():
        raise RuntimeError(f"Duplicate registered_domain in {asset.csv_path}")
    scores = frame["consensus_score"].dropna()
    if not scores.between(0, 100, inclusive="both").all():
        raise RuntimeError(f"consensus_score outside [0, 100] in {asset.csv_path}")
    if frame["snapshot_date"].isna().any() or set(
        frame["snapshot_date"].astype(str).unique()
    ) != {asset.snapshot_date}:
        raise RuntimeError(f"Snapshot date mismatch in {asset.csv_path}")
    if frame["methodology_version"].isna().any() or set(
        frame["methodology_version"].astype(str).unique()
    ) != {asset.methodology_version}:
        raise RuntimeError(f"Methodology version mismatch in {asset.csv_path}")


def load_release_history(
    assets: list[ReleaseAsset],
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    latest = pd.read_csv(
        assets[-1].csv_path,
        usecols=INPUT_COLUMNS,
        dtype={
            "registered_domain": "string",
            "consensus_score": "float32",
            "sources_count": "uint8",
            "ranking_sources_present": "string",
            "risk_sources_count": "uint8",
            "snapshot_date": "string",
            "methodology_version": "string",
        },
    )
    _validate_release_frame(latest, assets[-1])
    latest = latest.dropna(subset=["consensus_score"])
    if latest.empty:
        raise RuntimeError(f"Latest release has no scored domains: {assets[-1].csv_path}")
    latest = latest.set_index("registered_domain", drop=False)
    domains = latest.index

    score_columns: list[np.ndarray] = []
    source_count_columns: list[np.ndarray] = []
    source_mask_columns: list[np.ndarray] = []
    risk_count_columns: list[np.ndarray] = []
    assets_by_tag = {asset.tag: asset for asset in assets}
    for tag in history_tags(assets[-1].tag):
        asset = assets_by_tag.get(tag)
        if asset is None:
            print(f"Missing weekly snapshot {tag}; padding calendar slot", flush=True)
            score_columns.append(np.full(len(domains), np.nan, dtype=np.float32))
            source_count_columns.append(np.zeros(len(domains), dtype=np.uint8))
            source_mask_columns.append(np.zeros(len(domains), dtype=np.uint8))
            risk_count_columns.append(np.zeros(len(domains), dtype=np.uint8))
            continue

        print(f"Loading {asset.tag} ({asset.snapshot_date})", flush=True)
        frame = pd.read_csv(
            asset.csv_path,
            usecols=INPUT_COLUMNS,
            dtype={
                "registered_domain": "string",
                "consensus_score": "float32",
                "sources_count": "uint8",
                "ranking_sources_present": "string",
                "risk_sources_count": "uint8",
                "snapshot_date": "string",
                "methodology_version": "string",
            },
        )
        _validate_release_frame(frame, asset)
        aligned = frame.set_index("registered_domain").reindex(domains)
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


def rolling_components(values: np.ndarray, window: int) -> dict[str, np.ndarray]:
    block = values[:, -window:]
    observed = np.isfinite(block)
    filled = np.where(observed, block, 0.0).astype(np.float64, copy=False)
    count = observed.sum(axis=1)
    eligible = count >= max(3, math.ceil(window * MIN_HISTORY_SHARE))

    x = np.arange(window, dtype=np.float64)
    half_life = LEVEL_HALF_LIFE_WEEKS if window == LEVEL_WINDOW else window / 2
    weights = np.power(0.5, (window - 1 - x) / half_life)
    weighted_observed = observed * weights

    sum_y = filled.sum(axis=1)
    sum_x = (observed * x).sum(axis=1)
    sum_xx = (observed * x * x).sum(axis=1)
    sum_xy = (filled * x).sum(axis=1)
    sum_yy = (filled * filled).sum(axis=1)
    denominator = count * sum_xx - sum_x * sum_x
    slope = np.divide(
        count * sum_xy - sum_x * sum_y,
        denominator,
        out=np.zeros_like(sum_y),
        where=denominator > 0,
    )
    intercept = np.divide(
        sum_y - slope * sum_x,
        count,
        out=np.zeros_like(sum_y),
        where=count > 0,
    )
    residual_sse = np.maximum(
        sum_yy
        - 2 * intercept * sum_y
        - 2 * slope * sum_xy
        + intercept * intercept * count
        + 2 * intercept * slope * sum_x
        + slope * slope * sum_xx,
        0,
    )
    residual_std = np.sqrt(
        np.divide(
            residual_sse,
            count - 2,
            out=np.zeros_like(residual_sse),
            where=count > 2,
        )
    )
    centered_sum_xx = sum_xx - np.divide(
        sum_x * sum_x,
        count,
        out=np.zeros_like(sum_xx),
        where=count > 0,
    )
    slope_standard_error = np.divide(
        residual_std,
        np.sqrt(centered_sum_xx),
        out=np.full_like(residual_std, np.inf),
        where=centered_sum_xx > 0,
    )
    ewma = np.divide(
        (filled * weights).sum(axis=1),
        weighted_observed.sum(axis=1),
        out=np.zeros_like(sum_y),
        where=weighted_observed.sum(axis=1) > 0,
    )
    mean = np.divide(sum_y, count, out=np.zeros_like(sum_y), where=count > 0)
    return {
        "eligible": eligible,
        "observation_count": count,
        "mean": mean,
        "ewma": ewma,
        "fitted_current": intercept + slope * (window - 1),
        "slope": slope,
        "slope_standard_error": slope_standard_error,
        "residual_std": residual_std,
    }


def confirmed_slope(components: dict[str, np.ndarray]) -> np.ndarray:
    slope = np.clip(components["slope"], -MOMENTUM_SLOPE_CLIP, MOMENTUM_SLOPE_CLIP)
    threshold = MOMENTUM_CONFIDENCE_KAPPA * components["slope_standard_error"]
    return np.sign(slope) * np.maximum(np.abs(slope) - threshold, 0)


def ordinal_ranks(scores: np.ndarray) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    ranks = np.empty(len(scores), dtype=np.int32)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=np.int32)
    return ranks


def rank_bands(ranks: np.ndarray) -> np.ndarray:
    return np.searchsorted(RANK_BAND_LIMITS, ranks, side="left")


def scale_aware_adjustment(
    slope: np.ndarray,
    bands: np.ndarray,
    eligible: np.ndarray,
) -> np.ndarray:
    adjustment = np.zeros_like(slope)
    for band_index, cap in enumerate(RANK_BAND_CAPS):
        mask = eligible & (bands == band_index)
        if not mask.any():
            continue
        band_slope = slope[mask]
        center = np.median(band_slope)
        scale = max(1.4826 * np.median(np.abs(band_slope - center)), 0.05)
        adjustment[mask] = cap * np.tanh(band_slope / scale)
    return adjustment


def band_noise_confidence(
    residual_std: np.ndarray,
    bands: np.ndarray,
    eligible: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    confidence = np.zeros_like(residual_std)
    scales = np.full(len(RANK_BAND_LABELS), np.nan, dtype=np.float64)
    for band_index in range(len(RANK_BAND_LABELS)):
        mask = eligible & (bands == band_index)
        if not mask.any():
            continue
        scale = max(float(np.median(residual_std[mask])), 0.05)
        scales[band_index] = scale
        confidence[mask] = 1 / (1 + residual_std[mask] / scale)
    return confidence, scales


def source_set_confidence(scores: np.ndarray, masks: np.ndarray) -> np.ndarray:
    comparable = np.isfinite(scores[:, :-1]) & np.isfinite(scores[:, 1:])
    unchanged = comparable & (masks[:, :-1] == masks[:, 1:])
    comparable_count = comparable.sum(axis=1)
    return np.divide(
        unchanged.sum(axis=1),
        comparable_count,
        out=np.zeros(len(scores), dtype=np.float64),
        where=comparable_count > 0,
    )


def risk_states(current: np.ndarray, recent: np.ndarray) -> np.ndarray:
    return np.select(
        (current >= 2, current == 1, (current == 0) & (recent > 0)),
        ("multi-source-observed", "single-source-observed", "recent-history-only"),
        default="none-observed",
    )


def build_reputation_snapshot(assets: list[ReleaseAsset]) -> ReputationBuild:
    slot_tags = _validate_history_assets(assets)
    latest, scores, source_counts, source_masks, risk_counts = load_release_history(assets)
    latest_scores = scores[:, -1]
    ranks = ordinal_ranks(latest_scores)
    bands = rank_bands(ranks)

    level = rolling_components(scores, LEVEL_WINDOW)
    trend = rolling_components(scores, HISTORY_WINDOW)
    confirmed = confirmed_slope(trend)
    trend_adjustment = scale_aware_adjustment(confirmed, bands, trend["eligible"])
    material_trend = np.abs(trend_adjustment) >= 0.1 * RANK_BAND_CAPS[bands]
    noise_confidence, noise_scales = band_noise_confidence(
        trend["residual_std"], bands, trend["eligible"]
    )

    observed = np.isfinite(scores)
    history_observations = observed.sum(axis=1)
    history_confidence = history_observations / HISTORY_WINDOW
    coverage_confidence = np.divide(
        np.where(observed, source_counts, 0).sum(axis=1),
        history_observations * MAX_RANKING_SOURCES,
        out=np.zeros(len(scores), dtype=np.float64),
        where=history_observations > 0,
    )
    composition_confidence = source_set_confidence(scores, source_masks)
    reputation_confidence = np.prod(
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

    trend_state = np.select(
        (
            trend["eligible"] & material_trend & (confirmed > 0),
            trend["eligible"] & material_trend & (confirmed < 0),
            trend["eligible"] & (noise_confidence >= 0.5),
        ),
        ("rising", "falling", "stable"),
        default="uncertain",
    )
    structural_shock = (
        np.isfinite(scores[:, -2]) & (source_masks[:, -2] != source_masks[:, -1])
    )
    previous_level = rolling_components(scores[:, :-1], LEVEL_WINDOW)
    can_damp = structural_shock & previous_level["eligible"] & level["eligible"]
    candidate_score = np.where(
        can_damp,
        previous_level["ewma"]
        + SHOCK_DAMPING * (level["ewma"] - previous_level["ewma"]),
        level["ewma"],
    )
    candidate_score = np.clip(candidate_score, 0, 100)
    risk_state = risk_states(risk_counts[:, -1], risk_counts.max(axis=1))

    frame = pd.DataFrame(
        {
            "registered_domain": latest.index.to_numpy(),
            "reputation_score": level["ewma"],
            "reputation_confidence": reputation_confidence,
            "reputation_trend": trend_state,
            "trend_strength": confirmed,
            "history_observations": history_observations,
            "structural_shock": np.where(structural_shock, "true", "false"),
            "ddri_score_candidate": candidate_score,
            "observed_risk": risk_state,
            "snapshot_date": assets[-1].snapshot_date,
            "ddri_methodology_version": DDRI_METHODOLOGY_VERSION,
        }
    )
    frame.loc[~level["eligible"], ["reputation_score", "ddri_score_candidate"]] = np.nan
    frame.loc[~trend["eligible"], ["reputation_confidence", "trend_strength"]] = np.nan
    frame = frame.loc[:, PUBLIC_COLUMNS].sort_values(
        ["ddri_score_candidate", "reputation_score", "registered_domain"],
        ascending=[False, False, True],
        na_position="last",
        kind="mergesort",
        ignore_index=True,
    )
    validate_reputation_snapshot(frame)
    return ReputationBuild(
        frame=frame,
        noise_scales=noise_scales,
        input_assets=tuple(assets),
        history_tags=slot_tags,
    )


def validate_reputation_snapshot(frame: pd.DataFrame) -> None:
    if tuple(frame.columns) != PUBLIC_COLUMNS:
        raise RuntimeError(f"Unexpected DDRI public columns: {list(frame.columns)}")
    if frame.empty:
        raise RuntimeError("DDRI snapshot is empty")
    if frame["registered_domain"].isna().any() or frame["registered_domain"].duplicated().any():
        raise RuntimeError("DDRI registered_domain must be non-null and unique")
    for column in ("reputation_score", "ddri_score_candidate"):
        values = frame[column].dropna()
        if not values.between(0, 100, inclusive="both").all():
            raise RuntimeError(f"{column} outside [0, 100]")
    confidence = frame["reputation_confidence"].dropna()
    if not confidence.between(0, 1, inclusive="both").all():
        raise RuntimeError("reputation_confidence outside [0, 1]")
    if not set(frame["reputation_trend"]).issubset(
        {"rising", "falling", "stable", "uncertain"}
    ):
        raise RuntimeError("Unexpected reputation_trend value")
    if not set(frame["structural_shock"]).issubset({"true", "false"}):
        raise RuntimeError("Unexpected structural_shock value")


def write_deterministic_csv_gz(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", fileobj=raw_file, mode="wb", mtime=0) as gzip_file:
            with io.TextIOWrapper(gzip_file, encoding="utf-8", newline="") as text_file:
                frame.to_csv(
                    text_file,
                    index=False,
                    float_format="%.6f",
                    lineterminator="\n",
                    na_rep="",
                )


def quantile_summary(values: pd.Series) -> dict[str, float | None]:
    clean = values.dropna()
    if clean.empty:
        return {"p10": None, "p50": None, "p90": None}
    quantiles = clean.quantile((0.1, 0.5, 0.9))
    return {
        "p10": float(quantiles.loc[0.1]),
        "p50": float(quantiles.loc[0.5]),
        "p90": float(quantiles.loc[0.9]),
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        converted = float(value)
        return converted if math.isfinite(converted) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
