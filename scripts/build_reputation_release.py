"""Build and optionally publish an experimental aggregate-only DDRI archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from reputation_index import (
    DDRI_METHODOLOGY_VERSION,
    HISTORY_WINDOW,
    LEVEL_HALF_LIFE_WEEKS,
    LEVEL_WINDOW,
    MIN_HISTORY_SHARE,
    MOMENTUM_CONFIDENCE_KAPPA,
    MOMENTUM_SLOPE_CLIP,
    PUBLIC_COLUMNS,
    RANK_BAND_CAPS,
    RANK_BAND_LABELS,
    SHOCK_DAMPING,
    build_reputation_snapshot,
    discover_release_assets,
    json_safe,
    quantile_summary,
    sha256_file,
    write_deterministic_csv_gz,
)


DEFAULT_LATEST_RELEASE_TAG = "data-latest"


@dataclass(frozen=True)
class ArchiveResult:
    snapshot_date: str
    row_count: int
    csv_path: str
    metadata_path: str
    release_tag: str
    published: bool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def write_metadata(
    metadata_path: Path,
    csv_path: Path,
    build,
    github_run_id: str | None,
    git_commit_sha: str | None,
) -> None:
    frame = build.frame
    assets = build.input_assets
    trend_counts = frame["reputation_trend"].value_counts().to_dict()
    risk_counts = frame["observed_risk"].value_counts().to_dict()
    payload = {
        "created_at": utc_now().isoformat(),
        "status": "experimental",
        "snapshot_date": assets[-1].snapshot_date,
        "ddri_methodology_version": DDRI_METHODOLOGY_VERSION,
        "consensus_methodology_version": assets[-1].methodology_version,
        "row_count": len(frame),
        "public_columns": list(PUBLIC_COLUMNS),
        "sort_order": [
            {"column": "ddri_score_candidate", "direction": "DESC", "nulls": "LAST"},
            {"column": "reputation_score", "direction": "DESC", "nulls": "LAST"},
            {"column": "registered_domain", "direction": "ASC"},
        ],
        "methodology": {
            "input_scope": "public aggregate domain consensus releases only",
            "history_window_weeks": HISTORY_WINDOW,
            "level_window_weeks": LEVEL_WINDOW,
            "level_half_life_weeks": LEVEL_HALF_LIFE_WEEKS,
            "minimum_history_share": MIN_HISTORY_SHARE,
            "trend_confirmation_standard_errors": MOMENTUM_CONFIDENCE_KAPPA,
            "trend_slope_clip_points_per_week": MOMENTUM_SLOPE_CLIP,
            "rank_band_labels": list(RANK_BAND_LABELS),
            "rank_band_trend_caps": list(RANK_BAND_CAPS),
            "structural_shock_field": "ranking_sources_present",
            "structural_shock_damping": SHOCK_DAMPING,
            "confidence_interpretation": "exploratory heuristic in [0, 1], not a probability",
            "risk_interpretation": "observed public risk evidence, not proof of safety",
        },
        "inputs": [
            {
                "release_tag": asset.tag,
                "snapshot_date": asset.snapshot_date,
                "methodology_version": asset.methodology_version,
                "csv_asset": asset.csv_path.name,
                "csv_sha256": sha256_file(asset.csv_path),
                "metadata_asset": asset.metadata_path.name,
                "metadata_sha256": sha256_file(asset.metadata_path),
            }
            for asset in assets
        ],
        "statistics": {
            "reputation_score": quantile_summary(frame["reputation_score"]),
            "reputation_confidence": quantile_summary(frame["reputation_confidence"]),
            "ddri_score_candidate": quantile_summary(frame["ddri_score_candidate"]),
            "trend_counts": trend_counts,
            "risk_counts": risk_counts,
            "structural_shock_count": int((frame["structural_shock"] == "true").sum()),
            "complete_history_count": int(
                (frame["history_observations"] == HISTORY_WINDOW).sum()
            ),
            "rank_band_noise_scales": {
                label: scale
                for label, scale in zip(RANK_BAND_LABELS, build.noise_scales)
            },
        },
        "files": {
            "csv": csv_path.name,
            "csv_sha256": sha256_file(csv_path),
            "metadata": metadata_path.name,
        },
        "release": {
            "tag": assets[-1].tag,
            "prerelease": True,
        },
        "build": {
            "github_actions_run_id": github_run_id,
            "git_commit_sha": git_commit_sha,
        },
        "licensing_boundary": {
            "source_specific_values_published": False,
            "description": "Built exclusively from already-published aggregate release fields.",
        },
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_release_exists(tag: str, repo: str) -> None:
    command = ["gh", "release", "view", tag, "--repo", repo]
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GitHub release does not exist: {tag}")


def upload_assets(tag: str, repo: str, paths: list[Path]) -> None:
    ensure_release_exists(tag, repo)
    subprocess.run(
        [
            "gh",
            "release",
            "upload",
            tag,
            *(str(path) for path in paths),
            "--clobber",
            "--repo",
            repo,
        ],
        check=True,
    )


def publish(
    release_tag: str,
    latest_release_tag: str,
    repo: str,
    csv_path: Path,
    metadata_path: Path,
) -> None:
    upload_assets(release_tag, repo, [csv_path, metadata_path])
    latest_csv = csv_path.parent / "domain_reputation_experimental_latest.csv.gz"
    latest_metadata = metadata_path.parent / "meta_reputation_experimental_latest.json"
    shutil.copy2(csv_path, latest_csv)
    shutil.copy2(metadata_path, latest_metadata)
    upload_assets(latest_release_tag, repo, [latest_csv, latest_metadata])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/archive"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repo", help="GitHub repository, for example owner/name")
    parser.add_argument("--latest-release-tag", default=DEFAULT_LATEST_RELEASE_TAG)
    parser.add_argument("--github-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--git-commit-sha", default=os.environ.get("GITHUB_SHA"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.publish and not args.repo:
        raise RuntimeError("--repo is required with --publish")

    assets = discover_release_assets(args.release_root)
    build = build_reputation_snapshot(assets)
    snapshot_date = assets[-1].snapshot_date
    output_dir = args.output_dir / snapshot_date
    csv_path = output_dir / f"domain_reputation_experimental_{snapshot_date}.csv.gz"
    metadata_path = output_dir / f"meta_reputation_experimental_{snapshot_date}.json"

    write_deterministic_csv_gz(build.frame, csv_path)
    write_metadata(
        metadata_path,
        csv_path,
        build,
        github_run_id=args.github_run_id,
        git_commit_sha=args.git_commit_sha,
    )
    if args.publish:
        publish(
            release_tag=assets[-1].tag,
            latest_release_tag=args.latest_release_tag,
            repo=args.repo,
            csv_path=csv_path,
            metadata_path=metadata_path,
        )

    result = ArchiveResult(
        snapshot_date=snapshot_date,
        row_count=len(build.frame),
        csv_path=str(csv_path),
        metadata_path=str(metadata_path),
        release_tag=assets[-1].tag,
        published=args.publish,
    )
    print(json.dumps(asdict(result), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
