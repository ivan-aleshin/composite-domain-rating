"""Download the public consensus release history needed by DDRI."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


WEEKLY_TAG_PATTERN = re.compile(r"^data-(\d{4})-W(\d{2})$")
CONSENSUS_ASSET_PATTERN = re.compile(
    r"^domain_consensus_\d{4}-\d{2}-\d{2}\.csv\.gz$"
)
METADATA_ASSET_PATTERN = re.compile(r"^meta_\d{4}-\d{2}-\d{2}\.json$")


def run_gh_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def weekly_tag_date(tag: str) -> date:
    match = WEEKLY_TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"Not a weekly data tag: {tag}")
    return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)


def select_history_tags(tags: list[str], current_tag: str, count: int) -> list[str]:
    if count < 1:
        raise ValueError("History count must be positive")
    weekly_tags = sorted(
        {tag for tag in tags if WEEKLY_TAG_PATTERN.fullmatch(tag)},
        key=weekly_tag_date,
    )
    if current_tag not in weekly_tags:
        raise RuntimeError(f"Current release tag not found: {current_tag}")
    current_date = weekly_tag_date(current_tag)
    first_date = current_date - timedelta(weeks=count - 1)
    selected = [
        tag
        for tag in weekly_tags
        if first_date <= weekly_tag_date(tag) <= current_date
    ]
    minimum_count = math.ceil(count * 0.75)
    if len(selected) < minimum_count:
        raise RuntimeError(
            f"Need at least {minimum_count} of {count} calendar-week releases through "
            f"{current_tag}, found {len(selected)}"
        )
    return selected


def resolve_current_tag(repo: str, requested_tag: str, work_dir: Path) -> str:
    if requested_tag != "data-latest":
        if not WEEKLY_TAG_PATTERN.fullmatch(requested_tag):
            raise RuntimeError(
                "--release-tag must be data-latest or an immutable data-YYYY-WNN tag"
            )
        return requested_tag

    latest_dir = work_dir / ".latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gh",
            "release",
            "download",
            "data-latest",
            "--repo",
            repo,
            "--pattern",
            "meta_latest.json",
            "--dir",
            str(latest_dir),
            "--clobber",
        ],
        check=True,
    )
    metadata = json.loads((latest_dir / "meta_latest.json").read_text(encoding="utf-8"))
    current_tag = str((metadata.get("release") or {}).get("tag") or "")
    if not WEEKLY_TAG_PATTERN.fullmatch(current_tag):
        raise RuntimeError(f"Invalid weekly release tag in meta_latest.json: {current_tag}")
    return current_tag


def select_release_asset_names(names: list[str], tag: str) -> tuple[str, str]:
    consensus = sorted(name for name in names if CONSENSUS_ASSET_PATTERN.fullmatch(name))
    metadata = sorted(name for name in names if METADATA_ASSET_PATTERN.fullmatch(name))
    if len(consensus) != 1 or len(metadata) != 1:
        raise RuntimeError(
            f"Expected one consensus CSV and one base metadata asset in {tag}; "
            f"found {len(consensus)} and {len(metadata)}"
        )
    return consensus[0], metadata[0]


def release_asset_names(repo: str, tag: str) -> tuple[str, str]:
    payload = run_gh_json(["release", "view", tag, "--repo", repo, "--json", "assets"])
    names = [str(asset.get("name") or "") for asset in payload.get("assets", [])]
    return select_release_asset_names(names, tag)


def download_release(repo: str, tag: str, output_root: Path) -> None:
    consensus_name, metadata_name = release_asset_names(repo, tag)
    release_dir = output_root / tag
    release_dir.mkdir(parents=True, exist_ok=True)
    for asset_name in (consensus_name, metadata_name):
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repo,
                "--pattern",
                asset_name,
                "--dir",
                str(release_dir),
                "--clobber",
            ],
            check=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--release-tag", default="data-latest")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-list-limit", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    current_tag = resolve_current_tag(args.repo, args.release_tag, args.output_dir)
    releases = run_gh_json(
        [
            "release",
            "list",
            "--repo",
            args.repo,
            "--limit",
            str(args.release_list_limit),
            "--json",
            "tagName",
        ]
    )
    tags = [str(release.get("tagName") or "") for release in releases]
    selected = select_history_tags(tags, current_tag, args.count)
    for tag in selected:
        print(f"Downloading {tag}", flush=True)
        download_release(args.repo, tag, args.output_dir)
    print(json.dumps({"current_tag": current_tag, "release_tags": selected}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
