"""Download public ranking sources and write local lineage metadata."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
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
CLOUDFLARE_RADAR_DATASET_URL = "https://api.cloudflare.com/client/v4/radar/datasets/ranking_top_{bucket}"
CLOUDFLARE_RADAR_BUCKETS = (200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000)
OPENPAGERANK_TOP_10M_URL = "https://www.domcop.com/files/top/top10milliondomains.csv.zip"
LOCAL_CACHE_STALE_MAX_AGE_DAYS = 14
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_PROGRESS_INTERVAL_ROWS = 250_000
RETRIABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_ALL_SOURCES = ("tranco", "majestic", "cloudflare", "opr")
IMPLEMENTED_SOURCES = DEFAULT_ALL_SOURCES
NORMALIZED_CSV_NAMES = {
    "tranco": "tranco_domains.csv",
    "majestic": "majestic_domains.csv",
    "cloudflare": "cloudflare_domains.csv",
    "opr": "opr_domains.csv",
}


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


def retry_delay_seconds(
    response: requests.Response | None,
    attempt: int,
    backoff_seconds: float,
) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
    return backoff_seconds * (2 ** (attempt - 1))


def should_retry_http_error(error: requests.HTTPError) -> bool:
    response = error.response
    if response is None:
        return True
    return response.status_code in RETRIABLE_HTTP_STATUS_CODES


def download_text_with_retries(
    url: str,
    max_attempts: int,
    backoff_seconds: float,
    headers: dict[str, str] | None = None,
) -> str:
    last_error: requests.RequestException | None = None

    for attempt in range(1, max_attempts + 1):
        response: requests.Response | None = None
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except requests.HTTPError as error:
            last_error = error
            if not should_retry_http_error(error) or attempt == max_attempts:
                break
            time.sleep(retry_delay_seconds(response, attempt, backoff_seconds))
        except requests.RequestException as error:
            last_error = error
            if attempt == max_attempts:
                break
            time.sleep(retry_delay_seconds(response, attempt, backoff_seconds))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to download {url}")


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


def normalize_cloudflare_radar_csv(raw_csv_path: Path, normalized_csv_path: Path) -> tuple[int, int, int]:
    row_count = 0
    duplicate_count = 0
    best_bucket_by_domain: dict[str, int] = {}
    buckets_by_domain: dict[str, set[int]] = {}

    with raw_csv_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            raw_domain = row.get("domain")
            rank_bucket = _int_or_none(row.get("rank_bucket"))
            if raw_domain is None or rank_bucket is None:
                continue

            row_count += 1
            registered_domain = normalize_registered_domain(raw_domain)
            if registered_domain is None:
                continue

            buckets_by_domain.setdefault(registered_domain, set()).add(rank_bucket)
            previous_bucket = best_bucket_by_domain.get(registered_domain)
            if previous_bucket is None or rank_bucket < previous_bucket:
                if previous_bucket is not None:
                    duplicate_count += 1
                best_bucket_by_domain[registered_domain] = rank_bucket
            else:
                duplicate_count += 1

    normalized_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["registered_domain", "rank_bucket", "buckets_seen"])
        for registered_domain, rank_bucket in sorted(
            best_bucket_by_domain.items(),
            key=lambda item: (item[1], item[0]),
        ):
            writer.writerow([registered_domain, rank_bucket, len(buckets_by_domain[registered_domain])])

    return row_count, len(best_bucket_by_domain), duplicate_count


def _normalized_header(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _row_value(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    values_by_header = {_normalized_header(key): value for key, value in row.items()}
    for candidate in candidates:
        value = values_by_header.get(_normalized_header(candidate))
        if value not in {None, ""}:
            return value
    return None


def normalize_openpagerank_zip(
    zip_path: Path,
    normalized_csv_path: Path,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL_ROWS,
) -> tuple[int, int, int]:
    row_count = 0
    duplicate_count = 0
    seen_registered_domains: set[str] = set()

    with zipfile.ZipFile(zip_path) as archive:
        csv_members = [name for name in archive.namelist() if name.endswith(".csv")]
        if not csv_members:
            raise ValueError(f"No CSV file found in {zip_path}")

        normalized_csv_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(csv_members[0]) as raw_file:
            text_file = (line.decode("utf-8-sig") for line in raw_file)
            reader = csv.DictReader(text_file)
            with normalized_csv_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["registered_domain", "openpagerank_decimal", "openpagerank_integer", "openpagerank_rank"])
                for row in reader:
                    row_count += 1
                    if progress_interval > 0 and row_count % progress_interval == 0:
                        print(f"Processed {row_count:,} OpenPageRank rows", file=sys.stderr)

                    raw_domain = _row_value(row, ("domain", "Domain"))
                    registered_domain = normalize_registered_domain(raw_domain)
                    if registered_domain is None:
                        continue

                    page_rank_decimal = _float_or_none(
                        _row_value(row, ("open page rank", "openpagerank", "page rank value", "page_rank_decimal"))
                    )
                    page_rank_integer = _int_or_none(
                        _row_value(row, ("open page rank rounded", "page rank value rounded", "page_rank_integer"))
                    )
                    openpagerank_rank = _int_or_none(
                        _row_value(row, ("rank", "Rank", "global rank", "openpagerank_rank"))
                    )
                    if page_rank_decimal is None and page_rank_integer is None and openpagerank_rank is None:
                        continue

                    if registered_domain in seen_registered_domains:
                        duplicate_count += 1
                    else:
                        seen_registered_domains.add(registered_domain)

                    writer.writerow(
                        [
                            registered_domain,
                            page_rank_decimal if page_rank_decimal is not None else "",
                            page_rank_integer if page_rank_integer is not None else "",
                            openpagerank_rank if openpagerank_rank is not None else "",
                        ]
                    )

    return row_count, len(seen_registered_domains), duplicate_count


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        normalized_file_name = NORMALIZED_CSV_NAMES.get(str(payload.get("source")))
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

        result = missing_source_result("tranco", snapshot_date, error, now)
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


def download_cloudflare(
    output_root: Path,
    snapshot_date: date,
    simulate_missing: bool = False,
    allow_local_stale_cache: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> SourceResult:
    now = utc_now()
    source_dir = output_root / "cloudflare"
    snapshot_dir = source_dir / snapshot_date.isoformat()

    try:
        if simulate_missing:
            raise RuntimeError("Simulated missing source: cloudflare")

        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not api_token:
            raise RuntimeError("CLOUDFLARE_API_TOKEN is required for Cloudflare Radar downloads")

        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw_csv_path = snapshot_dir / "cloudflare_radar_buckets.csv"
        headers = {"Authorization": f"Bearer {api_token}"}
        row_count = 0

        with raw_csv_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["domain", "rank_bucket"])
            for bucket in CLOUDFLARE_RADAR_BUCKETS:
                url = CLOUDFLARE_RADAR_DATASET_URL.format(bucket=bucket)
                payload = download_text_with_retries(
                    url,
                    max_attempts=max_attempts,
                    backoff_seconds=retry_backoff_seconds,
                    headers=headers,
                )
                reader = csv.DictReader(payload.splitlines())
                for row in reader:
                    domain = row.get("domain")
                    if domain:
                        writer.writerow([domain, bucket])
                        row_count += 1

        normalized_csv_path = snapshot_dir / "cloudflare_domains.csv"
        _, valid_row_count, duplicate_count = normalize_cloudflare_radar_csv(raw_csv_path, normalized_csv_path)
        result = SourceResult(
            source="cloudflare",
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

        result = missing_source_result("cloudflare", snapshot_date, error, now)
        write_meta(snapshot_dir, result)
        return result


def download_opr(
    output_root: Path,
    snapshot_date: date,
    simulate_missing: bool = False,
    allow_local_stale_cache: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> SourceResult:
    now = utc_now()
    source_dir = output_root / "opr"
    snapshot_dir = source_dir / snapshot_date.isoformat()

    try:
        if simulate_missing:
            raise RuntimeError("Simulated missing source: opr")

        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw_zip_path = snapshot_dir / "top10milliondomains.csv.zip"
        if raw_zip_path.exists():
            with zipfile.ZipFile(raw_zip_path) as archive:
                if not any(name.endswith(".csv") for name in archive.namelist()):
                    raise ValueError(f"No CSV file found in existing {raw_zip_path}")
        else:
            download_file_with_retries(
                OPENPAGERANK_TOP_10M_URL,
                raw_zip_path,
                max_attempts=max_attempts,
                backoff_seconds=retry_backoff_seconds,
            )

        normalized_csv_path = snapshot_dir / "opr_domains.csv"
        row_count, valid_row_count, duplicate_count = normalize_openpagerank_zip(raw_zip_path, normalized_csv_path)
        result = SourceResult(
            source="opr",
            status="fresh",
            snapshot_date=snapshot_date.isoformat(),
            downloaded_at=now.isoformat(),
            list_id=None,
            file_hash=sha256_file(raw_zip_path),
            row_count=row_count,
            valid_row_count=valid_row_count,
            duplicate_registered_domains=duplicate_count,
            raw_file_path=str(raw_zip_path),
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

        result = missing_source_result("opr", snapshot_date, error, now)
        write_meta(snapshot_dir, result)
        return result


DOWNLOADERS = {
    "tranco": download_tranco,
    "majestic": download_majestic,
    "cloudflare": download_cloudflare,
    "opr": download_opr,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", choices=IMPLEMENTED_SOURCES, help="Download a single source")
    source_group.add_argument("--all", action="store_true", help="Download all default production sources")
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
        choices=IMPLEMENTED_SOURCES,
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
    sources = list(DEFAULT_ALL_SOURCES) if args.all else [args.source]

    results = []
    for source in sources:
        results.append(
            DOWNLOADERS[source](
                output_root=args.output_dir,
                snapshot_date=args.date,
                simulate_missing=source in args.simulate_missing,
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
