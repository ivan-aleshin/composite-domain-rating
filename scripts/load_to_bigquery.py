"""Load normalized source files into BigQuery raw tables."""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import NotFound


SOURCE_UPDATE_LOG_TABLE = "source_update_log"
DEFAULT_MAXIMUM_BYTES_BILLED = 21_474_836_480

STALE_TTL_DAYS = {
    "tranco": 14,
    "majestic": 14,
    "cloudflare": 14,
    "opr": 90,
    "crux": 45,
    "urlhaus": 7,
    "threatfox": 7,
    "phishtank": 7,
}


@dataclass(frozen=True)
class LoadResult:
    source: str
    status: str
    snapshot_date: str
    row_count: int
    age_days: int | None
    raw_table: str
    error: str | None


@dataclass(frozen=True)
class SourceLoadConfig:
    source: str
    raw_table: str
    staging_table: str
    normalized_csv_name: str
    load_schema: tuple[bigquery.SchemaField, ...]


SOURCE_LOAD_CONFIGS = {
    "tranco": SourceLoadConfig(
        source="tranco",
        raw_table="tranco_domains",
        staging_table="tranco_domains__staging",
        normalized_csv_name="tranco_domains.csv",
        load_schema=(
            bigquery.SchemaField("registered_domain", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("tranco_rank", "INT64", mode="REQUIRED"),
        ),
    ),
    "majestic": SourceLoadConfig(
        source="majestic",
        raw_table="majestic_domains",
        staging_table="majestic_domains__staging",
        normalized_csv_name="majestic_domains.csv",
        load_schema=(
            bigquery.SchemaField("registered_domain", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("ref_subnets", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("subdomains_seen", "INT64", mode="REQUIRED"),
        ),
    ),
    "cloudflare": SourceLoadConfig(
        source="cloudflare",
        raw_table="cloudflare_domains",
        staging_table="cloudflare_domains__staging",
        normalized_csv_name="cloudflare_domains.csv",
        load_schema=(
            bigquery.SchemaField("registered_domain", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("rank_bucket", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("buckets_seen", "INT64", mode="REQUIRED"),
        ),
    ),
    "opr": SourceLoadConfig(
        source="opr",
        raw_table="opr_domains",
        staging_table="opr_domains__staging",
        normalized_csv_name="opr_domains.csv",
        load_schema=(
            bigquery.SchemaField("registered_domain", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("openpagerank_decimal", "FLOAT64"),
            bigquery.SchemaField("openpagerank_integer", "INT64"),
            bigquery.SchemaField("openpagerank_rank", "INT64"),
        ),
    ),
}
IMPLEMENTED_SOURCES = tuple(SOURCE_LOAD_CONFIGS)


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


def ensure_dataset(client: bigquery.Client, project: str, dataset_name: str, location: str) -> None:
    dataset = bigquery.Dataset(f"{project}.{dataset_name}")
    dataset.location = location
    client.create_dataset(dataset, exists_ok=True)


def ensure_source_update_log(client: bigquery.Client, project: str, meta_dataset: str) -> None:
    table = bigquery.Table(
        table_ref(project, meta_dataset, SOURCE_UPDATE_LOG_TABLE),
        schema=[
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("update_started_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("update_completed_at", "TIMESTAMP"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("row_count", "INT64"),
            bigquery.SchemaField("age_days", "INT64"),
            bigquery.SchemaField("source_metadata", "JSON"),
            bigquery.SchemaField("error_message", "STRING"),
        ],
    )
    client.create_table(table, exists_ok=True)


def ensure_empty_raw_table(
    client: bigquery.Client,
    project: str,
    raw_dataset: str,
    source: str,
    maximum_bytes_billed: int,
) -> None:
    config = SOURCE_LOAD_CONFIGS[source]
    null_columns = [
        f"CAST(NULL AS {field.field_type}) AS {field.name}"
        for field in config.load_schema
    ]
    null_columns.extend(
        [
            "CAST(NULL AS DATE) AS snapshot_date",
            "CAST(NULL AS TIMESTAMP) AS ingested_at",
        ]
    )
    select_columns = ",\n        ".join(null_columns)
    query = f"""
    CREATE OR REPLACE TABLE {quoted_table(project, raw_dataset, config.raw_table)}
    PARTITION BY snapshot_date
    CLUSTER BY registered_domain AS
    SELECT
        {select_columns}
    FROM (SELECT 1)
    WHERE FALSE
    """
    client.query(query, job_config=query_config(maximum_bytes_billed)).result()


def raw_table_name(source: str) -> str:
    return SOURCE_LOAD_CONFIGS[source].raw_table


def raw_table_exists(client: bigquery.Client, project: str, raw_dataset: str, table_name: str) -> bool:
    try:
        client.get_table(table_ref(project, raw_dataset, table_name))
        return True
    except NotFound:
        return False


def read_local_meta(input_root: Path, source: str, snapshot_date: date) -> dict[str, Any]:
    meta_path = input_root / source / snapshot_date.isoformat() / "_meta.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def normalized_csv_path(input_root: Path, source: str, snapshot_date: date, metadata: dict[str, Any]) -> Path:
    metadata_path = metadata.get("normalized_csv_path")
    if metadata_path:
        return Path(metadata_path)
    return input_root / source / snapshot_date.isoformat() / SOURCE_LOAD_CONFIGS[source].normalized_csv_name


def insert_source_update(
    client: bigquery.Client,
    project: str,
    meta_dataset: str,
    source: str,
    started_at: datetime,
    status: str,
    row_count: int | None,
    age_days: int | None,
    source_metadata: dict[str, Any] | None,
    error_message: str | None,
) -> None:
    row = {
        "source": source,
        "update_started_at": started_at.isoformat(),
        "update_completed_at": utc_now().isoformat(),
        "status": status,
        "row_count": row_count,
        "age_days": age_days,
        "source_metadata": source_metadata,
        "error_message": error_message,
    }
    payload = io.BytesIO((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    client.load_table_from_file(
        payload,
        table_ref(project, meta_dataset, SOURCE_UPDATE_LOG_TABLE),
        job_config=job_config,
    ).result()


def last_successful_update(
    client: bigquery.Client,
    project: str,
    meta_dataset: str,
    source: str,
    maximum_bytes_billed: int,
) -> tuple[datetime, int | None] | None:
    query = f"""
    SELECT update_completed_at, row_count
    FROM {quoted_table(project, meta_dataset, SOURCE_UPDATE_LOG_TABLE)}
    WHERE source = @source
      AND status = 'success'
      AND update_completed_at IS NOT NULL
    ORDER BY update_completed_at DESC
    LIMIT 1
    """
    job_config = query_config(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("source", "STRING", source),
        ],
    )
    rows = list(client.query(query, job_config=job_config).result())
    if not rows:
        return None
    return rows[0].update_completed_at, rows[0].row_count


def load_source_csv(
    client: bigquery.Client,
    project: str,
    raw_dataset: str,
    source: str,
    csv_path: Path,
    snapshot_date: date,
    maximum_bytes_billed: int,
) -> int:
    config = SOURCE_LOAD_CONFIGS[source]
    staging_table = table_ref(project, raw_dataset, config.staging_table)
    load_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        schema=list(config.load_schema),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    with csv_path.open("rb") as file:
        load_job = client.load_table_from_file(file, staging_table, job_config=load_config)
    load_job.result()

    source_columns = ",\n        ".join(field.name for field in config.load_schema)
    query = f"""
    CREATE OR REPLACE TABLE {quoted_table(project, raw_dataset, config.raw_table)}
    PARTITION BY snapshot_date
    CLUSTER BY registered_domain AS
    SELECT
        {source_columns},
        @snapshot_date AS snapshot_date,
        CURRENT_TIMESTAMP() AS ingested_at
    FROM {quoted_table(project, raw_dataset, config.staging_table)}
    """
    job_config = query_config(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date),
        ],
    )
    client.query(query, job_config=job_config).result()
    client.delete_table(staging_table, not_found_ok=True)
    return int(load_job.output_rows or 0)


def handle_failed_fresh_source(
    client: bigquery.Client,
    project: str,
    raw_dataset: str,
    meta_dataset: str,
    source: str,
    snapshot_date: date,
    started_at: datetime,
    source_metadata: dict[str, Any] | None,
    error_message: str,
    maximum_bytes_billed: int,
) -> LoadResult:
    last_success = last_successful_update(
        client,
        project,
        meta_dataset,
        source,
        maximum_bytes_billed,
    )
    target_raw_table = raw_table_name(source)
    raw_exists = raw_table_exists(client, project, raw_dataset, target_raw_table)
    ttl_days = STALE_TTL_DAYS[source]

    if last_success is not None and raw_exists:
        completed_at, row_count = last_success
        age_days = (utc_now() - completed_at).days
        if age_days <= ttl_days:
            insert_source_update(
                client=client,
                project=project,
                meta_dataset=meta_dataset,
                source=source,
                started_at=started_at,
                status="stale",
                row_count=row_count,
                age_days=age_days,
                source_metadata=source_metadata,
                error_message=error_message,
            )
            return LoadResult(
                source=source,
                status="stale",
                snapshot_date=snapshot_date.isoformat(),
                row_count=int(row_count or 0),
                age_days=age_days,
                raw_table=table_ref(project, raw_dataset, target_raw_table),
                error=error_message,
            )

    ensure_empty_raw_table(client, project, raw_dataset, source, maximum_bytes_billed)
    insert_source_update(
        client=client,
        project=project,
        meta_dataset=meta_dataset,
        source=source,
        started_at=started_at,
        status="missing",
        row_count=0,
        age_days=None,
        source_metadata=source_metadata,
        error_message=error_message,
    )
    return LoadResult(
        source=source,
        status="missing",
        snapshot_date=snapshot_date.isoformat(),
        row_count=0,
        age_days=None,
        raw_table=table_ref(project, raw_dataset, target_raw_table),
        error=error_message,
    )


def load_source(
    client: bigquery.Client,
    project: str,
    raw_dataset: str,
    meta_dataset: str,
    source: str,
    input_root: Path,
    snapshot_date: date,
    maximum_bytes_billed: int,
) -> LoadResult:
    started_at = utc_now()
    ensure_source_update_log(client, project, meta_dataset)
    metadata = None

    try:
        metadata = read_local_meta(input_root, source, snapshot_date)
        if metadata.get("status") != "fresh":
            raise RuntimeError(f"Local {source} download status is {metadata.get('status')!r}, not 'fresh'")

        csv_path = normalized_csv_path(input_root, source, snapshot_date, metadata)
        if not csv_path.exists():
            raise FileNotFoundError(f"Normalized {source} CSV not found: {csv_path}")

        row_count = load_source_csv(
            client,
            project,
            raw_dataset,
            source,
            csv_path,
            snapshot_date,
            maximum_bytes_billed,
        )
    except Exception as error:
        if metadata is None:
            try:
                metadata = read_local_meta(input_root, source, snapshot_date)
            except (FileNotFoundError, json.JSONDecodeError):
                pass

        return handle_failed_fresh_source(
            client=client,
            project=project,
            raw_dataset=raw_dataset,
            meta_dataset=meta_dataset,
            source=source,
            snapshot_date=snapshot_date,
            started_at=started_at,
            source_metadata=metadata,
            error_message=str(error),
            maximum_bytes_billed=maximum_bytes_billed,
        )

    insert_source_update(
        client=client,
        project=project,
        meta_dataset=meta_dataset,
        source=source,
        started_at=started_at,
        status="success",
        row_count=row_count,
        age_days=0,
        source_metadata=metadata,
        error_message=None,
    )
    return LoadResult(
        source=source,
        status="fresh",
        snapshot_date=snapshot_date.isoformat(),
        row_count=row_count,
        age_days=0,
        raw_table=table_ref(project, raw_dataset, raw_table_name(source)),
        error=None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=IMPLEMENTED_SOURCES, required=True, help="Load a single source")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Snapshot date, YYYY-MM-DD")
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"), help="Raw data input directory")
    parser.add_argument("--project", help="GCP project id; defaults to application default credentials project")
    parser.add_argument("--location", default="US", help="BigQuery location")
    parser.add_argument("--raw-dataset", default="raw", help="BigQuery raw dataset")
    parser.add_argument("--meta-dataset", default="meta", help="BigQuery metadata dataset")
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

    ensure_dataset(client, project, args.raw_dataset, args.location)
    ensure_dataset(client, project, args.meta_dataset, args.location)

    result = load_source(
        client=client,
        project=project,
        raw_dataset=args.raw_dataset,
        meta_dataset=args.meta_dataset,
        source=args.source,
        input_root=args.input_dir,
        snapshot_date=args.date,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
