"""Download public ranking sources and write local lineage metadata."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from domain_utils import normalize_registered_domain


TRANCO_API_DATE_URL = "https://tranco-list.eu/api/lists/date/{snapshot_date}"
TRANCO_TOP_1M_URL = "https://tranco-list.eu/top-1m.csv.zip"
MAJESTIC_MILLION_URL = "https://downloads.majestic.com/majestic_million.csv"
LOCAL_CACHE_STALE_MAX_AGE_DAYS = 14
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class SourceResult:
    source: str
    status: str
    snapshot_date: str
    downloaded_at: str
    list_id: str | None
    file_hash: str | None
    row_count: int
    valid_row_count: int
    duplicate_registered_domains: int
    raw_file_path: str | None
    normalized_csv_path: str | None
    error: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def write_meta(output_dir: Path, result: SourceResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    meta_path = output_dir / "_meta.json"
    payload = asdict(result)
    payload["written_at"] = utc_now().isoformat()
    meta_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tranco_list_id(snapshot_date: date) -> str | None:
    url = TRANCO_API_DATE_URL.format(snapshot_date=snapshot_date.isoformat())
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException:
        return None

    payload: Any = response.json()
    if isinstance(payload, dict):
        for key in ("list_id", "id"):
            value = payload.get(key)
            if value:
                return str(value)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return None


def download_file(url: str, destination: Path) -> None:
    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        with destination.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def download_file_with_retries(
    url: str,
    destination: Path,
    max_attempts: int,
    backoff_seconds: float,
) -> None:
    """Download a file with simple exponential backoff."""
    last_error: requests.RequestException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            download_file(url, destination)
            return
        except requests.RequestException as error:
            last_error = error
            if attempt == max_attempts:
                break
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to download {url}")


def normalize_tranco_zip(zip_path: Path, normalized_csv_path: Path) -> tuple[int, int, int]:
    row_count = 0
    duplicate_count = 0
    best_rank_by_domain: dict[str, int] = {}

    with zipfile.ZipFile(zip_path) as archive:
        csv_members = [name for name in archive.namelist() if name.endswith(".csv")]
        if not csv_members:
            raise ValueError(f"No CSV file found in {zip_path}")

        with archive.open(csv_members[0]) as raw_file:
            text_file = (line.decode("utf-8-sig") for line in raw_file)
            reader = csv.reader(text_file)
            for row in reader:
                if len(row) < 2:
                    continue
                try:
                    tranco_rank = int(row[0])
                except ValueError:
                    continue

                row_count += 1
                registered_domain = normalize_registered_domain(row[1])
                if registered_domain is None:
                    continue

                previous_rank = best_rank_by_domain.get(registered_domain)
                if previous_rank is None or tranco_rank < previous_rank:
                    if previous_rank is not None:
                        duplicate_count += 1
                    best_rank_by_domain[registered_domain] = tranco_rank
                else:
                    duplicate_count += 1

    normalized_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["registered_domain", "tranco_rank"])
        for registered_domain, tranco_rank in sorted(best_rank_by_domain.items(), key=lambda item: item[1]):
            writer.writerow([registered_domain, tranco_rank])

    return row_count, len(best_rank_by_domain), duplicate_count


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def normalize_majestic_csv(raw_csv_path: Path, normalized_csv_path: Path) -> tuple[int, int, int]:
    row_count = 0
    duplicate_count = 0
    best_ref_subnets_by_domain: dict[str, int] = {}
    raw_domains_by_registered_domain: dict[str, set[str]] = {}

    with raw_csv_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            raw_domain = row.get("Domain") or row.get("domain")
            ref_subnets = _int_or_none(row.get("RefSubNets") or row.get("ref_subnets"))
            if raw_domain is None or ref_subnets is None:
                continue

            row_count += 1
            registered_domain = normalize_registered_domain(raw_domain)
            if registered_domain is None:
                continue

            raw_domains_by_registered_domain.setdefault(registered_domain, set()).add(raw_domain.strip().lower())
            previous_ref_subnets = best_ref_subnets_by_domain.get(registered_domain)
            if previous_ref_subnets is None or ref_subnets > previous_ref_subnets:
                if previous_ref_subnets is not None:
                    duplicate_count += 1
                best_ref_subnets_by_domain[registered_domain] = ref_subnets
            else:
                duplicate_count += 1

    normalized_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["registered_domain", "ref_subnets", "subdomains_seen"])
        for registered_domain, ref_subnets in sorted(
            best_ref_subnets_by_domain.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            writer.writerow(
                [
                    registered_domain,
                    ref_subnets,
                    len(raw_domains_by_registered_domain[registered_domain]),
                ]
            )

    return row_count, len(best_ref_subnets_by_domain), duplicate_count


def latest_valid_snapshot(source_dir: Path, now: datetime) -> Path | None:
    if not source_dir.exists():
        return None

    candidates: list[tuple[datetime, Path]] = []
    for meta_path in source_dir.glob("*/_meta.json"):
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            status = payload.get("status")
            downloaded_at = datetime.fromisoformat(payload["downloaded_at"])
        except (KeyError, ValueError, json.JSONDecodeError):
            continue

        if status not in {"fresh", "stale"}:
            continue
        if now - downloaded_at > timedelta(days=LOCAL_CACHE_STALE_MAX_AGE_DAYS):
            continue
        source = payload.get("source")
        normalized_file_name = {
            "tranco": "tranco_domains.csv",
            "majestic": "majestic_domains.csv",
        }.get(source)
        if normalized_file_name is None or not (meta_path.parent / normalized_file_name).exists():
            continue
        candidates.append((downloaded_at, meta_path.parent))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def stale_result(snapshot_dir: Path, requested_date: date, error: Exception, now: datetime) -> SourceResult:
    meta = json.loads((snapshot_dir / "_meta.json").read_text(encoding="utf-8"))
    return SourceResult(
        source=str(meta.get("source", "unknown")),
        status="stale",
        snapshot_date=requested_date.isoformat(),
        downloaded_at=now.isoformat(),
        list_id=meta.get("list_id"),
        file_hash=meta.get("file_hash"),
        row_count=int(meta.get("row_count", 0)),
        valid_row_count=int(meta.get("valid_row_count", 0)),
        duplicate_registered_domains=int(meta.get("duplicate_registered_domains", 0)),
        raw_file_path=meta.get("raw_file_path") or meta.get("raw_zip_path"),
        normalized_csv_path=meta.get("normalized_csv_path"),
        error=str(error),
    )


def missing_result(requested_date: date, error: Exception, now: datetime) -> SourceResult:
    return missing_source_result("tranco", requested_date, error, now)


def missing_source_result(source: str, requested_date: date, error: Exception, now: datetime) -> SourceResult:
    return SourceResult(
        source=source,
        status="missing",
        snapshot_date=requested_date.isoformat(),
        downloaded_at=now.isoformat(),
        list_id=None,
        file_hash=None,
        row_count=0,
        valid_row_count=0,
        duplicate_registered_domains=0,
        raw_file_path=None,
        normalized_csv_path=None,
        error=str(error),
    )


def download_tranco(
    output_root: Path,
    snapshot_date: date,
    simulate_missing: bool = False,
    allow_local_stale_cache: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> SourceResult:
    now = utc_now()
    source_dir = output_root / "tranco"
    snapshot_dir = source_dir / snapshot_date.isoformat()

    try:
        if simulate_missing:
            raise RuntimeError("Simulated missing source: tranco")

        snapshot_dir.mkdir(parents=True, exist_ok=True)
        list_id = tranco_list_id(snapshot_date)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_zip = Path(tmpdir) / "top-1m.csv.zip"
            download_file_with_retries(
                TRANCO_TOP_1M_URL,
                temp_zip,
                max_attempts=max_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            raw_file_path = snapshot_dir / "top-1m.csv.zip"
            shutil.move(str(temp_zip), raw_file_path)

        normalized_csv_path = snapshot_dir / "tranco_domains.csv"
        row_count, valid_row_count, duplicate_count = normalize_tranco_zip(raw_file_path, normalized_csv_path)
        result = SourceResult(
            source="tranco",
            status="fresh",
            snapshot_date=snapshot_date.isoformat(),
            downloaded_at=now.isoformat(),
            list_id=list_id,
            file_hash=sha256_file(raw_file_path),
            row_count=row_count,
            valid_row_count=valid_row_count,
            duplicate_registered_domains=duplicate_count,
            raw_file_path=str(raw_file_path),
            normalized_csv_path=str(normalized_csv_path),
            error=None,
        )
        write_meta(snapshot_dir, result)
        return result
    except Exception as error:
        if allow_local_stale_cache:
            fallback_dir = latest_valid_snapshot(source_dir, now)
            if fallback_dir is not None:
                result = stale_result(fallback_dir, snapshot_date, error, now)
                write_meta(snapshot_dir, result)
                return result

        result = missing_result(snapshot_date, error, now)
        write_meta(snapshot_dir, result)
        return result


def download_majestic(
    output_root: Path,
    snapshot_date: date,
    simulate_missing: bool = False,
    allow_local_stale_cache: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> SourceResult:
    now = utc_now()
    source_dir = output_root / "majestic"
    snapshot_dir = source_dir / snapshot_date.isoformat()

    try:
        if simulate_missing:
            raise RuntimeError("Simulated missing source: majestic")

        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw_csv_path = snapshot_dir / "majestic_million.csv"
        download_file_with_retries(
            MAJESTIC_MILLION_URL,
            raw_csv_path,
            max_attempts=max_attempts,
            backoff_seconds=retry_backoff_seconds,
        )

        normalized_csv_path = snapshot_dir / "majestic_domains.csv"
        row_count, valid_row_count, duplicate_count = normalize_majestic_csv(raw_csv_path, normalized_csv_path)
        result = SourceResult(
            source="majestic",
            status="fresh",
            snapshot_date=snapshot_date.isoformat(),
            downloaded_at=now.isoformat(),
            list_id=None,
            file_hash=sha256_file(raw_csv_path),
            row_count=row_count,
            valid_row_count=valid_row_count,
            duplicate_registered_domains=duplicate_count,
            raw_file_path=str(raw_csv_path),
            normalized_csv_path=str(normalized_csv_path),
            error=None,
        )
        write_meta(snapshot_dir, result)
        return result
    except Exception as error:
        if allow_local_stale_cache:
            fallback_dir = latest_valid_snapshot(source_dir, now)
            if fallback_dir is not None:
                result = stale_result(fallback_dir, snapshot_date, error, now)
                write_meta(snapshot_dir, result)
                return result

        result = missing_source_result("majestic", snapshot_date, error, now)
        write_meta(snapshot_dir, result)
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", choices=["tranco", "majestic"], help="Download a single source")
    source_group.add_argument("--all", action="store_true", help="Download all implemented sources")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Snapshot date, YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"), help="Raw data output directory")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Download attempts before marking the local download result missing",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help="Initial retry backoff in seconds; doubles after each failed attempt",
    )
    parser.add_argument(
        "--allow-local-stale-cache",
        action="store_true",
        help="Use recent local data/raw cache as a dev-only fallback after download failures",
    )
    parser.add_argument(
        "--simulate-missing",
        choices=["tranco", "majestic"],
        action="append",
        default=[],
        help="Simulate a source failure for resilience testing",
    )
    args = parser.parse_args()
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    if args.retry_backoff_seconds < 0:
        parser.error("--retry-backoff-seconds must be greater than or equal to 0")
    return args


def main() -> int:
    args = parse_args()
    sources = ["tranco", "majestic"] if args.all else [args.source]

    results = []
    for source in sources:
        if source == "tranco":
            results.append(
                download_tranco(
                    output_root=args.output_dir,
                    snapshot_date=args.date,
                    simulate_missing="tranco" in args.simulate_missing,
                    allow_local_stale_cache=args.allow_local_stale_cache,
                    max_attempts=args.max_attempts,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
            )
        elif source == "majestic":
            results.append(
                download_majestic(
                    output_root=args.output_dir,
                    snapshot_date=args.date,
                    simulate_missing="majestic" in args.simulate_missing,
                    allow_local_stale_cache=args.allow_local_stale_cache,
                    max_attempts=args.max_attempts,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
            )

    for result in results:
        print(json.dumps(asdict(result), sort_keys=True))

    return 0


if __name__ == "__main__":
    sys.exit(main())
