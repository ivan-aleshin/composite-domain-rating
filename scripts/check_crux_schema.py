"""Smoke-check the CrUX public BigQuery source contract."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from google.cloud import bigquery


DEFAULT_MAXIMUM_BYTES_BILLED = 21_474_836_480
CRUX_PROJECT = "chrome-ux-report"
CRUX_DATASET = "experimental"
CRUX_TABLE = "global"
EXPECTED_BUCKETS = (
    1_000,
    5_000,
    10_000,
    50_000,
    100_000,
    500_000,
    1_000_000,
    5_000_000,
    10_000_000,
    50_000_000,
)


@dataclass(frozen=True)
class CruxCheckResult:
    status: str
    crux_yyyymm: int
    origin_rows: int
    registered_domains: int
    bucket_count: int
    buckets: list[int]
    warnings: list[str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_crux_yyyymm(now: datetime | None = None) -> int:
    """Match the conservative dbt default in macros/crux_utils.sql."""
    now = now or utc_now()
    target_year = now.year
    if now.day >= 14:
        target_month = now.month - 1
    else:
        target_month = now.month - 2
    if target_month <= 0:
        target_year -= 1
        target_month += 12
    return target_year * 100 + target_month


def table_id() -> str:
    return f"{CRUX_PROJECT}.{CRUX_DATASET}.{CRUX_TABLE}"


def quoted_table_id() -> str:
    return f"`{table_id()}`"


def query_config(maximum_bytes_billed: int) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(maximum_bytes_billed=maximum_bytes_billed)


def nested_field_exists(fields: list[bigquery.SchemaField], path: tuple[str, ...]) -> bool:
    current_fields = fields
    for index, part in enumerate(path):
        field = next((item for item in current_fields if item.name == part), None)
        if field is None:
            return False
        if index == len(path) - 1:
            return True
        current_fields = list(field.fields)
    return False


def validate_schema(client: bigquery.Client) -> None:
    table = client.get_table(table_id())
    schema = list(table.schema)
    required_paths = (
        ("yyyymm",),
        ("origin",),
        ("experimental", "popularity", "rank"),
    )
    missing = [path for path in required_paths if not nested_field_exists(schema, path)]
    if missing:
        rendered = ", ".join(".".join(path) for path in missing)
        raise RuntimeError(f"CrUX schema check failed: missing required field(s): {rendered}")


def validate_data(
    client: bigquery.Client,
    crux_yyyymm: int,
    maximum_bytes_billed: int,
    fail_on_bucket_change: bool,
) -> CruxCheckResult:
    query = f"""
    WITH source AS (
        SELECT
            origin,
            LOWER(NET.REG_DOMAIN(NET.HOST(origin))) AS registered_domain,
            experimental.popularity.rank AS crux_rank_bucket
        FROM {quoted_table_id()}
        WHERE
            yyyymm = @crux_yyyymm
            AND experimental.popularity.rank IS NOT NULL
    )
    SELECT
        COUNT(*) AS origin_rows,
        COUNT(DISTINCT registered_domain) AS registered_domains,
        ARRAY_AGG(DISTINCT crux_rank_bucket ORDER BY crux_rank_bucket) AS buckets
    FROM source
    WHERE registered_domain IS NOT NULL
    """
    job_config = query_config(maximum_bytes_billed)
    job_config.query_parameters = [
        bigquery.ScalarQueryParameter("crux_yyyymm", "INT64", crux_yyyymm),
    ]
    rows = list(client.query(query, job_config=job_config).result())
    row = rows[0] if rows else None
    origin_rows = int(row.origin_rows or 0) if row else 0
    registered_domains = int(row.registered_domains or 0) if row else 0
    buckets = [int(bucket) for bucket in (row.buckets or [])] if row else []
    warnings: list[str] = []

    if origin_rows == 0 or registered_domains == 0:
        raise RuntimeError(
            "CrUX schema check failed: no rows found for "
            f"yyyymm={crux_yyyymm} with experimental.popularity.rank"
        )

    expected = set(EXPECTED_BUCKETS)
    observed = set(buckets)
    missing_buckets = sorted(expected - observed)
    unexpected_buckets = sorted(observed - expected)
    if missing_buckets:
        warnings.append(f"Missing expected bucket(s): {missing_buckets}")
    if unexpected_buckets:
        warnings.append(f"Unexpected bucket(s): {unexpected_buckets}")
    if warnings and fail_on_bucket_change:
        raise RuntimeError("CrUX bucket set changed: " + "; ".join(warnings))

    return CruxCheckResult(
        status="ok",
        crux_yyyymm=crux_yyyymm,
        origin_rows=origin_rows,
        registered_domains=registered_domains,
        bucket_count=len(buckets),
        buckets=buckets,
        warnings=warnings,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="GCP project id; defaults to application default credentials project")
    parser.add_argument("--location", default="US", help="BigQuery location")
    parser.add_argument("--crux-yyyymm", type=int, default=default_crux_yyyymm(), help="CrUX snapshot month, YYYYMM")
    parser.add_argument(
        "--fail-on-bucket-change",
        action="store_true",
        help="Fail if observed CrUX bucket values differ from the expected set",
    )
    parser.add_argument(
        "--maximum-bytes-billed",
        type=int,
        default=DEFAULT_MAXIMUM_BYTES_BILLED,
        help="Maximum bytes billed for the BigQuery validation query",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = bigquery.Client(project=args.project, location=args.location)
    validate_schema(client)
    result = validate_data(
        client=client,
        crux_yyyymm=args.crux_yyyymm,
        maximum_bytes_billed=args.maximum_bytes_billed,
        fail_on_bucket_change=args.fail_on_bucket_change,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    if result.warnings:
        print("CrUX schema check warning: " + "; ".join(result.warnings), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
