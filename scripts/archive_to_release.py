"""Export the public mart snapshot as release-ready archive files."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import NotFound


DEFAULT_MAXIMUM_BYTES_BILLED = 21_474_836_480
DEFAULT_MARTS_DATASET = "marts"
DEFAULT_META_DATASET = "meta"
DEFAULT_MART_TABLE = "mart_domain_consensus_score"
SOURCE_UPDATE_LOG_TABLE = "source_update_log"
PUBLIC_COLUMNS = (
    "registered_domain",
    "consensus_score",
    "coverage_tier",
    "sources_count",
    "ranking_sources_present",
    "tld_category",
    "is_spam_prone_tld",
    "security_flags_observed",
    "risk_sources_count",
    "threat_types",
    "last_threat_seen",
    "snapshot_date",
    "methodology_version",
)


@dataclass(frozen=True)
class ArchiveResult:
    snapshot_date: str
    row_count: int
    csv_path: str
    metadata_path: str
    release_tag: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def table_ref(project: str, dataset: str, table: str) -> str:
    return f"{project}.{dataset}.{table}"


def quoted_table(project: str, dataset: str, table: str) -> str:
    return f"`{table_ref(project, dataset, table)}`"


def query_config(
    maximum_bytes_billed: int,
    query_parameters: list[bigquery.ScalarQueryParameter] | None = None,
) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=query_parameters or [],
    )


def iso_week_tag(snapshot_date: date) -> str:
    iso_year, iso_week, _ = snapshot_date.isocalendar()
    return f"data-{iso_year}-W{iso_week:02d}"


def json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    return str(value)


def parse_source_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return json_safe(value)
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        if isinstance(payload, dict):
            return json_safe(payload)
        return {"raw": json_safe(payload)}
    return {"raw": json_safe(value)}


def ensure_table_exists(client: bigquery.Client, project: str, dataset: str, table: str) -> None:
    try:
        client.get_table(table_ref(project, dataset, table))
    except NotFound as error:
        raise RuntimeError(f"BigQuery table not found: {table_ref(project, dataset, table)}") from error


def latest_snapshot_date(
    client: bigquery.Client,
    project: str,
    marts_dataset: str,
    mart_table: str,
    maximum_bytes_billed: int,
) -> date:
    query = f"""
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM {quoted_table(project, marts_dataset, mart_table)}
    """
    rows = list(client.query(query, job_config=query_config(maximum_bytes_billed)).result())
    snapshot_date = rows[0].snapshot_date if rows else None
    if snapshot_date is None:
        raise RuntimeError(f"No snapshot_date values found in {table_ref(project, marts_dataset, mart_table)}")
    return snapshot_date


def export_public_csv(
    client: bigquery.Client,
    project: str,
    marts_dataset: str,
    mart_table: str,
    snapshot_date: date,
    csv_path: Path,
    maximum_bytes_billed: int,
    progress_interval: int,
) -> tuple[int, set[str]]:
    query = f"""
    SELECT
        registered_domain,
        consensus_score,
        coverage_tier,
        sources_count,
        ranking_sources_present,
        tld_category,
        is_spam_prone_tld,
        security_flags_observed,
        risk_sources_count,
        ARRAY_TO_STRING(threat_types, '|') AS threat_types,
        last_threat_seen,
        snapshot_date,
        methodology_version
    FROM {quoted_table(project, marts_dataset, mart_table)}
    WHERE snapshot_date = @snapshot_date
      AND (
          consensus_score IS NOT NULL
          OR sources_count >= 2
      )
    """
    job_config = query_config(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date),
        ],
    )
    row_count = 0
    methodology_versions: set[str] = set()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with gzip.open(csv_path, "wt", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=PUBLIC_COLUMNS)
        writer.writeheader()
        rows = client.query(query, job_config=job_config).result(page_size=100_000)
        for row in rows:
            payload = {column: csv_value(row[column]) for column in PUBLIC_COLUMNS}
            writer.writerow(payload)
            if row["methodology_version"] is not None:
                methodology_versions.add(str(row["methodology_version"]))
            row_count += 1
            if progress_interval > 0 and row_count % progress_interval == 0:
                print(f"Exported {row_count:,} rows to {csv_path}", file=sys.stderr)

    return row_count, methodology_versions


def latest_source_statuses(
    client: bigquery.Client,
    project: str,
    meta_dataset: str,
    maximum_bytes_billed: int,
) -> dict[str, dict[str, Any]]:
    ensure_table_exists(client, project, meta_dataset, SOURCE_UPDATE_LOG_TABLE)
    query = f"""
    WITH ranked AS (
        SELECT
            source,
            update_started_at,
            update_completed_at,
            status,
            row_count,
            age_days,
            source_metadata,
            error_message,
            ROW_NUMBER() OVER (
                PARTITION BY source
                ORDER BY update_completed_at DESC, update_started_at DESC
            ) AS row_number
        FROM {quoted_table(project, meta_dataset, SOURCE_UPDATE_LOG_TABLE)}
    )
    SELECT
        source,
        update_started_at,
        update_completed_at,
        status,
        row_count,
        age_days,
        source_metadata,
        error_message
    FROM ranked
    WHERE row_number = 1
    ORDER BY source
    """
    rows = client.query(query, job_config=query_config(maximum_bytes_billed)).result()
    statuses: dict[str, dict[str, Any]] = {}
    now = utc_now()
    for row in rows:
        load_status = str(row.status)
        metadata = parse_source_metadata(row.source_metadata)
        archive_age_days = None
        if row.update_completed_at is not None:
            archive_age_days = (now - row.update_completed_at).days
        statuses[str(row.source)] = {
            "status": "fresh" if load_status == "success" else load_status,
            "load_status": load_status,
            "update_started_at": json_safe(row.update_started_at),
            "update_completed_at": json_safe(row.update_completed_at),
            "row_count": row.row_count,
            "age_days": archive_age_days,
            "load_age_days": row.age_days,
            "source_metadata": metadata,
            "error_message": row.error_message,
        }
    return statuses


def crux_source_status(
    client: bigquery.Client,
    project: str,
    marts_dataset: str,
    mart_table: str,
    snapshot_date: date,
    maximum_bytes_billed: int,
) -> dict[str, Any]:
    query = f"""
    SELECT
        COUNTIF(p_crux IS NOT NULL) AS row_count,
        MIN(crux_rank_bucket) AS best_rank_bucket,
        MAX(crux_rank_bucket) AS worst_rank_bucket,
        MAX(crux_snapshot_date) AS crux_snapshot_date
    FROM {quoted_table(project, marts_dataset, mart_table)}
    WHERE snapshot_date = @snapshot_date
    """
    job_config = query_config(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date),
        ],
    )
    rows = list(client.query(query, job_config=job_config).result())
    row = rows[0] if rows else None
    row_count = int(row.row_count or 0) if row else 0
    crux_snapshot_date = row.crux_snapshot_date if row else None
    return {
        "status": "fresh" if row_count > 0 else "missing",
        "load_status": "public_bigquery_dataset",
        "update_started_at": None,
        "update_completed_at": json_safe(crux_snapshot_date),
        "row_count": row_count,
        "age_days": (utc_now().date() - crux_snapshot_date).days if crux_snapshot_date else None,
        "age_basis": "crux_month_start",
        "load_age_days": None,
        "source_metadata": {
            "source_type": "bigquery_public_dataset",
            "source_table": "chrome-ux-report.experimental.global",
            "freshness_note": "age_days is measured from the first day of the CrUX dataset month, not from publication time.",
            "snapshot_date": json_safe(crux_snapshot_date),
            "crux_yyyymm": crux_snapshot_date.strftime("%Y%m") if crux_snapshot_date else None,
            "best_rank_bucket": row.best_rank_bucket if row else None,
            "worst_rank_bucket": row.worst_rank_bucket if row else None,
        },
        "error_message": None if row_count > 0 else "No CrUX rows found in mart snapshot.",
    }


def write_lineage_json(
    metadata_path: Path,
    project: str,
    marts_dataset: str,
    mart_table: str,
    snapshot_date: date,
    row_count: int,
    methodology_versions: set[str],
    source_statuses: dict[str, dict[str, Any]],
    csv_path: Path,
    release_tag: str,
) -> None:
    payload = {
        "created_at": utc_now().isoformat(),
        "snapshot_date": snapshot_date.isoformat(),
        "methodology_version": ",".join(sorted(methodology_versions)) if methodology_versions else None,
        "row_count": row_count,
        "project": project,
        "mart_table": table_ref(project, marts_dataset, mart_table),
        "public_columns": list(PUBLIC_COLUMNS),
        "archive_policy": {
            "description": "Public archive includes scored rows and sparse rows observed by at least two ranking sources.",
            "included": "consensus_score IS NOT NULL OR sources_count >= 2",
            "excluded": "one-source-only rows",
            "internal_mart_scope": "full diagnostic source universe",
        },
        "files": {
            "csv": str(csv_path),
            "metadata": str(metadata_path),
        },
        "release": {
            "tag": release_tag,
            "prerelease": True,
        },
        "dbt_invocation_id": os.environ.get("DBT_INVOCATION_ID"),
        "sources": source_statuses,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_github_release(
    release_tag: str,
    snapshot_date: date,
    csv_path: Path,
    metadata_path: Path,
    repo: str | None,
) -> None:
    command = [
        "gh",
        "release",
        "create",
        release_tag,
        str(csv_path),
        str(metadata_path),
        "--prerelease",
        "--title",
        release_tag,
        "--notes",
        f"Derived domain consensus archive for {snapshot_date.isoformat()}.",
    ]
    if repo:
        command.extend(["--repo", repo])
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="GCP project id; defaults to application default credentials project")
    parser.add_argument("--location", default="US", help="BigQuery location")
    parser.add_argument("--marts-dataset", default=DEFAULT_MARTS_DATASET, help="BigQuery marts dataset")
    parser.add_argument("--meta-dataset", default=DEFAULT_META_DATASET, help="BigQuery metadata dataset")
    parser.add_argument("--mart-table", default=DEFAULT_MART_TABLE, help="BigQuery mart table to export")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, help="Snapshot date to export, YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=Path("data/archive"), help="Archive output directory")
    parser.add_argument("--release-tag", help="Release tag; defaults to data-YYYY-WNN from snapshot date")
    parser.add_argument("--create-release", action="store_true", help="Create a GitHub prerelease using gh CLI")
    parser.add_argument("--repo", help="GitHub repository for gh release create, e.g. owner/name")
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=250_000,
        help="Print export progress every N rows; set to 0 to disable",
    )
    parser.add_argument(
        "--maximum-bytes-billed",
        type=int,
        default=DEFAULT_MAXIMUM_BYTES_BILLED,
        help="Maximum bytes billed for BigQuery SQL jobs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = bigquery.Client(project=args.project, location=args.location)
    project = args.project or client.project

    ensure_table_exists(client, project, args.marts_dataset, args.mart_table)
    snapshot_date = args.snapshot_date or latest_snapshot_date(
        client=client,
        project=project,
        marts_dataset=args.marts_dataset,
        mart_table=args.mart_table,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    release_tag = args.release_tag or iso_week_tag(snapshot_date)
    output_dir = args.output_dir / snapshot_date.isoformat()
    csv_path = output_dir / f"domain_consensus_{snapshot_date.isoformat()}.csv.gz"
    metadata_path = output_dir / f"meta_{snapshot_date.isoformat()}.json"

    row_count, methodology_versions = export_public_csv(
        client=client,
        project=project,
        marts_dataset=args.marts_dataset,
        mart_table=args.mart_table,
        snapshot_date=snapshot_date,
        csv_path=csv_path,
        maximum_bytes_billed=args.maximum_bytes_billed,
        progress_interval=args.progress_interval,
    )
    if row_count == 0:
        raise RuntimeError(f"No rows exported for snapshot_date={snapshot_date.isoformat()}")
    if len(methodology_versions) > 1:
        versions = ", ".join(sorted(methodology_versions))
        raise RuntimeError(f"Multiple methodology versions found in one archive snapshot: {versions}")

    source_statuses = latest_source_statuses(
        client=client,
        project=project,
        meta_dataset=args.meta_dataset,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    source_statuses["crux"] = crux_source_status(
        client=client,
        project=project,
        marts_dataset=args.marts_dataset,
        mart_table=args.mart_table,
        snapshot_date=snapshot_date,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    write_lineage_json(
        metadata_path=metadata_path,
        project=project,
        marts_dataset=args.marts_dataset,
        mart_table=args.mart_table,
        snapshot_date=snapshot_date,
        row_count=row_count,
        methodology_versions=methodology_versions,
        source_statuses=source_statuses,
        csv_path=csv_path,
        release_tag=release_tag,
    )

    if args.create_release:
        create_github_release(
            release_tag=release_tag,
            snapshot_date=snapshot_date,
            csv_path=csv_path,
            metadata_path=metadata_path,
            repo=args.repo,
        )

    print(json.dumps(asdict(ArchiveResult(
        snapshot_date=snapshot_date.isoformat(),
        row_count=row_count,
        csv_path=str(csv_path),
        metadata_path=str(metadata_path),
        release_tag=release_tag,
    )), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
